"""
Coleta web do site do concorrente.

Estratégia simples e robusta: tenta caminhos comuns de RSS/Atom em ordem.
Se nenhum responder 200 com XML válido, devolve [] (sem crash).

Não fazemos scraping de HTML — feeds são suficientes pra detectar conteúdo
novo/temas. Se o concorrente não publica feed, o agente foca só no IG.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import httpx

TIMEOUT = 12.0
COMMON_PATHS = ["/feed", "/feed/", "/rss", "/rss.xml", "/blog/feed", "/blog/rss", "/atom.xml"]


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
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None


def _try_url(url: str) -> list[dict] | None:
    try:
        r = httpx.get(
            url,
            timeout=TIMEOUT,
            headers={"User-Agent": "MergeCompetitorBot/1.0"},
            follow_redirects=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if r.status_code != 200:
        return None
    txt = r.text
    if "<rss" not in txt and "<feed" not in txt:
        return None
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return None
    items: list[dict] = []
    # RSS 2.0
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = _parse_pubdate(it.findtext("pubDate") or "")
        if title:
            items.append({"title": title[:300], "link": link, "published_at": pub})
    # Atom fallback
    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("{http://www.w3.org/2005/Atom}title")
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            pub_el = entry.find("{http://www.w3.org/2005/Atom}published")
            upd_el = entry.find("{http://www.w3.org/2005/Atom}updated")
            title = (title_el.text or "").strip() if title_el is not None else ""
            link = (link_el.get("href") if link_el is not None else "") or ""
            pub_raw = (pub_el.text if pub_el is not None else None) or (
                upd_el.text if upd_el is not None else None
            )
            pub = _parse_pubdate(pub_raw or "")
            if title:
                items.append({"title": title[:300], "link": link, "published_at": pub})
    return items if items else None


def fetch_site_recent(site: str, days: int = 7) -> list[dict]:
    """Tenta achar feed do site e retorna posts dos últimos `days`."""
    if not site:
        return []
    base = site.rstrip("/")
    items: list[dict] | None = None
    for path in COMMON_PATHS:
        items = _try_url(base + path)
        if items:
            break
    if not items:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent: list[dict] = []
    for it in items:
        pub = it.get("published_at")
        if pub is None:
            recent.append({"title": it["title"], "link": it["link"], "published_at": ""})
            continue
        if pub >= cutoff:
            recent.append(
                {
                    "title": it["title"],
                    "link": it["link"],
                    "published_at": pub.isoformat(),
                }
            )
    return recent
