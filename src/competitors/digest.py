"""
Competitor Intelligence Agent — pipeline semanal.

Schedule (ver scheduler.py): toda sexta a partir das 16:00 BRT, 1x/semana.

Pipeline por competitor:
  1. fetch IG via Business Discovery (instagram.py)
  2. fetch site RSS (web.py)
  3. dedup contra snapshot anterior pra extrair "posts novos da semana"
  4. computa engajamento médio
  5. classifica temas via Claude Sonnet (classifier.py)

Output:
  output/competitors_weekly.md   — relatório completo (overwrite)
  output/competitors_state.json  — snapshot pra próxima rodada
  Telegram digest                 — resumo executivo via alerts.notify()

Conversa Telegram dedicada — Pedro deve PINNAR a mensagem de digest pra
separar do canal principal de criativos. Identificador `[Competitor]`
em todo alerta facilita filtro visual.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alerts import notify

from . import classifier, config, instagram, web

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT_MD = ROOT / "output" / "competitors_weekly.md"
STATE_FILE = ROOT / "output" / "competitors_state.json"
DIGEST_STATE = ROOT / "output" / ".last_competitors_digest.txt"

TZ = ZoneInfo("America/Sao_Paulo")
DIGEST_WEEKDAY = 4  # 0=Mon ... 4=Fri
DIGEST_HOUR = 16
LOOKBACK_DAYS = 7


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _gather_one(competitor: dict, prev_state: dict) -> dict:
    """Coleta IG + web + classifica. Retorna dict com tudo pra render do MD."""
    name = competitor["name"]
    handle = competitor.get("handle", "")
    site = competitor.get("site", "")

    bd = instagram.fetch_handle(handle) if handle else None
    media_all = (bd or {}).get("media", {}).get("data", []) if bd else []
    media_recent = instagram.filter_recent(media_all, days=LOOKBACK_DAYS)

    # diff vs snapshot anterior — IDs nunca antes vistos
    prev_ids = set(prev_state.get("seen_ig_ids", []))
    new_ig_ids = [m.get("id") for m in media_recent if m.get("id") and m.get("id") not in prev_ids]

    eng = instagram.avg_engagement(media_recent)

    site_items = web.fetch_site_recent(site, days=LOOKBACK_DAYS) if site else []
    prev_links = set(prev_state.get("seen_site_links", []))
    new_site_links = [it.get("link") for it in site_items if it.get("link") and it.get("link") not in prev_links]

    # texts para classificar
    texts: list[str] = []
    for m in media_recent:
        cap = (m.get("caption") or "").strip()
        if cap:
            texts.append(cap)
    for it in site_items:
        if it.get("title"):
            texts.append(it["title"])
    themes = classifier.cluster_themes(texts, name) if texts else []

    return {
        "name": name,
        "handle": handle,
        "site": site,
        "notes": competitor.get("notes", ""),
        "ig_followers": (bd or {}).get("followers_count"),
        "ig_total_media": (bd or {}).get("media_count"),
        "ig_recent_count": len(media_recent),
        "ig_new_count": len(new_ig_ids),
        "ig_recent_media": media_recent,
        "ig_engagement": eng,
        "site_recent_count": len(site_items),
        "site_new_count": len(new_site_links),
        "site_recent": site_items,
        "themes": themes,
        # snapshot do estado pós-rodada
        "_state": {
            "seen_ig_ids": [m.get("id") for m in media_all if m.get("id")][:200],
            "seen_site_links": [it.get("link") for it in site_items if it.get("link")][:200],
        },
    }


def _fmt_int(n) -> str:
    if n is None:
        return "—"
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


def _render_md(rows: list[dict], now: datetime) -> str:
    lines: list[str] = []
    lines.append(f"# Competitor Intelligence · semana {now.strftime('%d/%m/%Y')}")
    lines.append("")
    lines.append(f"_Janela: últimos {LOOKBACK_DAYS} dias · gerado {now.strftime('%d/%m/%Y %H:%M')} BRT_")
    lines.append("")
    lines.append("## Resumo")
    lines.append("")
    lines.append("| Concorrente | Followers | Posts/sem | Novos | Eng. médio | Posts blog |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        eng = r["ig_engagement"]
        lines.append(
            f"| **{r['name']}** "
            f"| {_fmt_int(r['ig_followers'])} "
            f"| {r['ig_recent_count']} "
            f"| {r['ig_new_count']} "
            f"| {int(eng['avg_engagement'])} "
            f"| {r['site_recent_count']} |"
        )
    lines.append("")

    for r in rows:
        lines.append(f"## {r['name']}")
        if r.get("notes"):
            lines.append(f"_{r['notes']}_")
        lines.append("")
        if r["handle"]:
            lines.append(
                f"- IG: [@{r['handle']}](https://instagram.com/{r['handle']}) · "
                f"{_fmt_int(r['ig_followers'])} followers · "
                f"{r['ig_recent_count']} posts em {LOOKBACK_DAYS}d "
                f"({r['ig_new_count']} novos vs último snapshot)"
            )
            eng = r["ig_engagement"]
            if eng["posts"]:
                lines.append(
                    f"- Engajamento médio: **{int(eng['avg_engagement'])}** "
                    f"(likes {int(eng['avg_likes'])} · comments {int(eng['avg_comments'])})"
                )
        if r["site"]:
            lines.append(
                f"- Site: {r['site']} · {r['site_recent_count']} posts no blog "
                f"({r['site_new_count']} novos)"
            )

        if r["themes"]:
            lines.append("")
            lines.append("**Temas recorrentes:**")
            for t in r["themes"]:
                exemplo = f" — _{t['exemplo']}_" if t.get("exemplo") else ""
                lines.append(f"- `{t['tema']}` ×{t['frequencia']}{exemplo}")

        if r["ig_recent_media"]:
            lines.append("")
            lines.append("**Top posts IG (por engajamento):**")
            ranked = sorted(
                r["ig_recent_media"],
                key=lambda m: (int(m.get("like_count") or 0) + int(m.get("comments_count") or 0)),
                reverse=True,
            )[:5]
            for m in ranked:
                cap = (m.get("caption") or "").replace("\n", " ").strip()
                if len(cap) > 140:
                    cap = cap[:137] + "..."
                likes = int(m.get("like_count") or 0)
                comments = int(m.get("comments_count") or 0)
                permalink = m.get("permalink", "")
                lines.append(f"- [{likes}❤ · {comments}💬]({permalink}) {cap}")

        if r["site_recent"]:
            lines.append("")
            lines.append("**Headlines blog:**")
            for it in r["site_recent"][:8]:
                t = it.get("title", "")
                link = it.get("link", "")
                lines.append(f"- [{t}]({link})" if link else f"- {t}")

        lines.append("")

    return "\n".join(lines) + "\n"


def _render_telegram_digest(rows: list[dict], now: datetime) -> str:
    """Resumo curto pro Telegram. HTML mode."""
    lines: list[str] = []
    lines.append(f"📡 <b>[Competitor] semana {now.strftime('%d/%m')}</b>")
    lines.append(f"<i>{LOOKBACK_DAYS}d · {len(rows)} concorrentes</i>")
    lines.append("")

    # Top 3 por novos posts (sinal de atividade)
    by_activity = sorted(rows, key=lambda r: r["ig_new_count"] + r["site_new_count"], reverse=True)
    lines.append("<b>+ ativos</b>")
    for r in by_activity[:3]:
        lines.append(
            f"· <b>{html.escape(r['name'])}</b> — "
            f"{r['ig_new_count']} IG · {r['site_new_count']} blog"
        )

    # Top 3 por engajamento médio
    by_eng = sorted(
        [r for r in rows if r["ig_engagement"]["posts"]],
        key=lambda r: r["ig_engagement"]["avg_engagement"],
        reverse=True,
    )
    if by_eng:
        lines.append("")
        lines.append("<b>+ engajamento</b>")
        for r in by_eng[:3]:
            lines.append(
                f"· <b>{html.escape(r['name'])}</b> — "
                f"{int(r['ig_engagement']['avg_engagement'])} eng/post"
            )

    # Temas em alta — junta todos e pega top 5 por frequencia somada
    theme_map: dict[str, int] = {}
    for r in rows:
        for t in r["themes"]:
            theme_map[t["tema"]] = theme_map.get(t["tema"], 0) + t["frequencia"]
    if theme_map:
        lines.append("")
        lines.append("<b>temas em alta</b>")
        top_themes = sorted(theme_map.items(), key=lambda kv: kv[1], reverse=True)[:5]
        for tema, freq in top_themes:
            lines.append(f"· {html.escape(tema)} ×{freq}")

    lines.append("")
    lines.append("<i>relatório completo em output/competitors_weekly.md</i>")
    return "\n".join(lines)


def run_once(now: datetime | None = None) -> dict:
    """Executa o pipeline. Retorna stats."""
    now = now or datetime.now(TZ)
    competitors = config.load_competitors()
    if not competitors:
        print("competitors · nenhum concorrente configurado, pulando")
        return {"competitors": 0}

    print(f"competitors · iniciando snapshot de {len(competitors)} concorrentes")
    state = _load_state()
    by_handle = state.get("by_handle", {}) if isinstance(state.get("by_handle"), dict) else {}

    rows: list[dict] = []
    new_state_by_handle: dict = {}
    for c in competitors:
        key = c.get("handle") or c["name"].lower()
        prev = by_handle.get(key, {}) if isinstance(by_handle.get(key), dict) else {}
        try:
            row = _gather_one(c, prev)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ competitors · {c['name']} falhou: {e!r}")
            continue
        rows.append(row)
        new_state_by_handle[key] = row.pop("_state")

    if not rows:
        print("competitors · sem dados coletados")
        return {"competitors": 0}

    md = _render_md(rows, now)
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_MD.write_text(md, encoding="utf-8")
    print(f"competitors · relatório escrito em {OUTPUT_MD}")

    _save_state(
        {
            "last_run": now.isoformat(timespec="seconds"),
            "by_handle": new_state_by_handle,
        }
    )

    digest = _render_telegram_digest(rows, now)
    notify(digest, force=True)

    return {
        "competitors": len(rows),
        "ig_new_total": sum(r["ig_new_count"] for r in rows),
        "site_new_total": sum(r["site_new_count"] for r in rows),
    }


def maybe_run(now: datetime) -> bool:
    """Friday >=16h BRT, 1x/semana. Idempotente via DIGEST_STATE."""
    if now.weekday() != DIGEST_WEEKDAY:
        return False
    if now.hour < DIGEST_HOUR:
        return False
    week_key = now.strftime("%G-W%V")  # ISO week (year-week)
    if DIGEST_STATE.exists():
        if DIGEST_STATE.read_text(encoding="utf-8").strip() == week_key:
            return False
    try:
        stats = run_once(now)
        print(
            f"competitors digest · concorrentes={stats.get('competitors',0)} "
            f"ig_new={stats.get('ig_new_total',0)} "
            f"site_new={stats.get('site_new_total',0)}"
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ competitors digest exception: {e!r}")
        return False
    DIGEST_STATE.parent.mkdir(parents=True, exist_ok=True)
    DIGEST_STATE.write_text(week_key, encoding="utf-8")
    return True
