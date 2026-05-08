"""
News watcher — varre os RSS de feeds.py de hora em hora.

Para cada item NOVO (não visto antes):
  1. Extrai title + summary + url + published
  2. Score via scorer (Claude)
  3. Roteia:
     - score >= 8 OU post_event=true → reactive.trigger_reactive_post()
     - 5 <= score < 8 → adiciona ao output/news_pool.json (usado por stories + planner)
     - score < 5 → ignora

State persistente:
  output/.news_seen.txt   (hash de cada item visto, append-only com cap)
  output/news_pool.json   (notícias na fila, ordenadas por score)

Cooldown reativo: máx 1 post reativo aprovado E publicado por dia (do reactive.py).
Watcher só DETECTA e dispara preview; cooldown se aplica em on_approve.
"""
from __future__ import annotations

import hashlib
import html as _html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from alerts import notify

from . import feeds as feeds_module
from . import scorer

ROOT = Path(__file__).resolve().parent.parent.parent
SEEN_FILE = ROOT / "output" / ".news_seen.txt"
POOL_FILE = ROOT / "output" / "news_pool.json"
WATCHER_STATE = ROOT / "output" / ".last_news_watcher.txt"

WATCH_INTERVAL_MINUTES = 60
SEEN_CAP = 5000
POOL_RETENTION_HOURS = 36


def _hash_item(feed_name: str, link: str, title: str) -> str:
    h = hashlib.sha1()
    h.update((feed_name + "|" + (link or "") + "|" + (title or "")).encode("utf-8"))
    return h.hexdigest()[:16]


def _load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    return set(SEEN_FILE.read_text(encoding="utf-8").splitlines())


def _add_seen(new_hashes: list[str]) -> None:
    if not new_hashes:
        return
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = list(_load_seen())
    existing.extend(new_hashes)
    if len(existing) > SEEN_CAP:
        existing = existing[-SEEN_CAP:]
    SEEN_FILE.write_text("\n".join(existing), encoding="utf-8")


def _load_pool() -> list[dict]:
    if not POOL_FILE.exists():
        return []
    try:
        return json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_pool(pool: list[dict]) -> None:
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    POOL_FILE.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def _strip(html_text: str) -> str:
    return re.sub(r"<[^>]+>", " ", _html.unescape(html_text or "")).strip()


def _parse_pubdate(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def fetch_feed(feed_cfg: dict) -> list[dict]:
    """Faz GET no RSS, parseia, retorna lista de items normalizados."""
    try:
        r = httpx.get(
            feed_cfg["url"],
            timeout=15.0,
            headers={"User-Agent": "MergeNewsBot/1.0"},
            follow_redirects=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ feed {feed_cfg['name']} fetch erro: {e!r}")
        return []
    if r.status_code != 200:
        print(f"⚠ feed {feed_cfg['name']} {r.status_code}")
        return []
    return _parse_rss(r.text, feed_cfg)


def _parse_rss(xml_text: str, feed_cfg: dict) -> list[dict]:
    """Parse RSS 2.0 + Atom. Robusto a feeds quebrados."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"⚠ feed {feed_cfg['name']} XML parse erro: {e}")
        return []

    items: list[dict] = []
    # RSS 2.0: <channel><item>...
    for item in root.iter("item"):
        title = _strip((item.findtext("title") or ""))
        link = (item.findtext("link") or "").strip()
        desc = _strip(item.findtext("description") or "")
        pub = _parse_pubdate(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or "")
        items.append(_normalize_item(title, link, desc, pub, feed_cfg))
    # Atom: {ns}entry
    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
            published_el = entry.find("{http://www.w3.org/2005/Atom}published")
            updated_el = entry.find("{http://www.w3.org/2005/Atom}updated")
            title = _strip(title_el.text if title_el is not None else "")
            link = (link_el.get("href") if link_el is not None else "") or ""
            desc = _strip(summary_el.text if summary_el is not None else "")
            pub_str = (published_el.text if published_el is not None else None) or (
                updated_el.text if updated_el is not None else None
            )
            pub = _parse_pubdate(pub_str or "")
            items.append(_normalize_item(title, link, desc, pub, feed_cfg))
    return items


def _normalize_item(title: str, link: str, summary: str, pub: datetime | None, cfg: dict) -> dict:
    return {
        "feed_name": cfg["name"],
        "category": cfg.get("category", ""),
        "modalities": cfg.get("modalities", []),
        "feed_relevance": cfg.get("weight_relevance", 0.5),
        "title": title[:300],
        "link": link,
        "summary": summary[:1000],
        "published_at": pub.isoformat() if pub else "",
        "hash": _hash_item(cfg["name"], link, title),
    }


def _is_recent(item: dict, max_age_hours: int = 24) -> bool:
    pub = item.get("published_at", "")
    if not pub:
        return True  # se feed não dá pubdate, assume recente
    try:
        dt = datetime.fromisoformat(pub)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt
    return age <= timedelta(hours=max_age_hours)


def _prune_pool(pool: list[dict]) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=POOL_RETENTION_HOURS)
    out = []
    for it in pool:
        added = it.get("added_at", "")
        try:
            dt = datetime.fromisoformat(added)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            dt = datetime.now(timezone.utc)
        if dt >= cutoff:
            out.append(it)
    return out


# ---------------------------------------------------------------------------

def watch_once() -> dict:
    """Uma passada completa. Retorna stats {fetched, new, scored, reactive, pooled}."""
    seen = _load_seen()
    pool = _prune_pool(_load_pool())
    new_hashes: list[str] = []
    new_items: list[dict] = []

    for cfg in feeds_module.FEEDS:
        items = fetch_feed(cfg)
        for it in items:
            if it["hash"] in seen:
                continue
            if not _is_recent(it):
                # ainda guarda hash pra não rescannear
                new_hashes.append(it["hash"])
                continue
            new_items.append(it)
            new_hashes.append(it["hash"])

    if not new_items:
        _add_seen(new_hashes)
        return {"fetched": len(seen) + len(new_hashes), "new": 0, "scored": 0, "reactive": 0, "pooled": 0}

    # Scoring em batch (1 chamada por item — Sonnet, fast)
    scored: list[dict] = []
    for it in new_items[:30]:  # cap por passada
        try:
            s = scorer.score_item(it)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ scorer falhou em '{it['title'][:50]}': {e!r}")
            continue
        it.update(s)
        scored.append(it)

    n_reactive = 0
    n_pooled = 0
    rejected: list[dict] = []
    # Lazy import pra evitar ciclo
    from . import reactive as reactive_mod

    for it in scored:
        score = float(it.get("score", 0))
        post_event = bool(it.get("post_event", False))

        if score >= 8 or (score >= 7 and post_event):
            try:
                reactive_mod.trigger_reactive_post(it)
                n_reactive += 1
            except Exception as e:  # noqa: BLE001
                print(f"⚠ reactive trigger falhou: {e!r}")
        elif score >= 5:
            it["added_at"] = datetime.now(timezone.utc).isoformat()
            pool.append(it)
            n_pooled += 1
        else:
            # Score < 5: dropado. Guarda meta pra debug.
            rejected.append({
                "title": (it.get("title") or "")[:80],
                "score": score,
                "reasoning": (it.get("reasoning") or "")[:120],
                "feed": it.get("feed_name", ""),
            })

    pool.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    _save_pool(pool)
    _add_seen(new_hashes)

    rejected.sort(key=lambda x: x["score"], reverse=True)
    return {
        "fetched": sum(1 for _ in feeds_module.FEEDS),
        "new": len(new_items),
        "scored": len(scored),
        "reactive": n_reactive,
        "pooled": n_pooled,
        "rejected": rejected[:5],
    }


def maybe_run(now: datetime) -> bool:
    """Roda no máx 1x por hora. Retorna True se rodou."""
    if WATCHER_STATE.exists():
        try:
            last = datetime.fromisoformat(WATCHER_STATE.read_text(encoding="utf-8").strip())
            if (now - last).total_seconds() < WATCH_INTERVAL_MINUTES * 60:
                return False
        except ValueError:
            pass
    try:
        stats = watch_once()
        print(
            f"news watcher · novos={stats['new']} scored={stats['scored']} "
            f"reactive={stats['reactive']} pooled={stats['pooled']}"
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ news watcher exception: {e!r}")
        return False
    WATCHER_STATE.parent.mkdir(parents=True, exist_ok=True)
    WATCHER_STATE.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    return True
