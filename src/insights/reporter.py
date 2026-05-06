"""
Reports diário (10h BRT) e mensal (último dia, 18h BRT) via Telegram.

Idempotente: usa state files em output/.last_insights_*.txt pra não reenviar.

Engajamento ponderado (escolhido pra dar peso ao que é raro):
  likes*1 + comments*3 + saves*2 + shares*4 + views*0.001
"""
from __future__ import annotations

import csv
import html
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from alerts import notify

ROOT = Path(__file__).resolve().parent.parent.parent
INSIGHTS = ROOT / "output" / "insights.csv"
DAILY_STATE = ROOT / "output" / ".last_insights_daily.txt"
MONTHLY_STATE = ROOT / "output" / ".last_insights_monthly.txt"

DAILY_HOUR = 10  # primeiro tick após 10h BRT manda
MONTHLY_HOUR = 18  # último dia do mês, após 18h


def _i(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _engagement(row: dict) -> float:
    return (
        _i(row.get("likes")) * 1
        + _i(row.get("comments")) * 3
        + _i(row.get("saved")) * 2
        + _i(row.get("shares")) * 4
        + _i(row.get("views")) * 0.001
    )


def _latest_per_post() -> list[dict]:
    """Última snapshot por media_id (mais recente). Snapshots são append-only."""
    if not INSIGHTS.exists():
        return []
    by_id: dict[str, dict] = {}
    with INSIGHTS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = row.get("media_id", "")
            if not mid:
                continue
            prev = by_id.get(mid)
            if prev is None or row["snapshot_date"] >= prev["snapshot_date"]:
                by_id[mid] = row
    return list(by_id.values())


def _filter_window(snaps: list[dict], days: int) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    return [s for s in snaps if s.get("snapshot_date", "") >= cutoff]


def _format_post_line(s: dict, include_eng: bool = False) -> str:
    pid = html.escape(s.get("post_id", "?"))
    parts = [
        f"<code>{pid}</code>",
        f"{_i(s.get('views'))} views",
        f"{_i(s.get('likes'))} likes",
        f"{_i(s.get('saved'))} saves",
    ]
    if include_eng:
        parts.append(f"eng={int(_engagement(s))}")
    return "· " + " · ".join(parts)


def _detect_pattern(top: list[dict]) -> str:
    """Heurística simples: predominância de format ou faixa de age_days."""
    if not top:
        return ""
    fmts = defaultdict(int)
    for s in top:
        fmts[s.get("format", "?")] += 1
    main_fmt, main_count = max(fmts.items(), key=lambda kv: kv[1])
    if main_count >= len(top) * 0.66:
        return f"padrão: <b>{main_fmt}</b> domina top da semana"
    return ""


def maybe_daily_report(now: datetime) -> bool:
    """Manda resumo dos últimos 7d na primeira passada após 10h BRT."""
    today = now.date().isoformat()
    if DAILY_STATE.exists() and DAILY_STATE.read_text(encoding="utf-8").strip() == today:
        return False
    if now.hour < DAILY_HOUR:
        return False
    snaps = _filter_window(_latest_per_post(), days=7)
    if not snaps:
        DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
        DAILY_STATE.write_text(today, encoding="utf-8")
        return False
    ranked = sorted(snaps, key=_engagement, reverse=True)
    top3 = ranked[:3]
    bot3 = list(reversed(ranked[-3:])) if len(ranked) > 3 else []

    lines = [f"📊 <b>Merge insights · {now.strftime('%d/%m')}</b> (últimos 7d)"]
    lines.append(f"\n<b>top 3</b>")
    for s in top3:
        lines.append(_format_post_line(s, include_eng=True))
    if bot3:
        lines.append(f"\n<b>bottom 3</b>")
        for s in bot3:
            lines.append(_format_post_line(s))
    pattern = _detect_pattern(top3)
    if pattern:
        lines.append(f"\n{pattern}")

    notify("\n".join(lines), silent=True)
    DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
    DAILY_STATE.write_text(today, encoding="utf-8")
    return True


def maybe_monthly_report(now: datetime) -> bool:
    """No último dia do mês, depois das 18h, manda resumo do mês."""
    next_day = now + timedelta(days=1)
    is_last_day = next_day.month != now.month
    if not is_last_day:
        return False
    if now.hour < MONTHLY_HOUR:
        return False
    month_key = now.strftime("%Y-%m")
    if MONTHLY_STATE.exists() and MONTHLY_STATE.read_text(encoding="utf-8").strip() == month_key:
        return False

    # Filtra posts publicados nesse mês.
    snaps = _latest_per_post()
    month_prefix = month_key + "-"
    in_month = [
        s for s in snaps
        if s.get("snapshot_date", "").startswith(month_prefix[:7])
    ]
    # Refina via age_days <= dias passados no mês
    days_in_month = now.day
    in_month = [s for s in in_month if _i(s.get("age_days")) <= days_in_month + 2]

    if not in_month:
        MONTHLY_STATE.parent.mkdir(parents=True, exist_ok=True)
        MONTHLY_STATE.write_text(month_key, encoding="utf-8")
        return False

    total_views = sum(_i(s.get("views")) for s in in_month)
    total_likes = sum(_i(s.get("likes")) for s in in_month)
    total_comments = sum(_i(s.get("comments")) for s in in_month)
    total_saves = sum(_i(s.get("saved")) for s in in_month)
    total_shares = sum(_i(s.get("shares")) for s in in_month)

    top5 = sorted(in_month, key=_engagement, reverse=True)[:5]

    lines = [
        f"🗓️ <b>Merge · fechamento {now.strftime('%B %Y')}</b>",
        f"posts: <b>{len(in_month)}</b>",
        f"views: <b>{total_views:,}</b>".replace(",", "."),
        f"likes: <b>{total_likes:,}</b>".replace(",", "."),
        f"comments: <b>{total_comments:,}</b>".replace(",", "."),
        f"saves: <b>{total_saves:,}</b>".replace(",", "."),
        f"shares: <b>{total_shares:,}</b>".replace(",", "."),
        f"\n<b>top 5 do mês</b>",
    ]
    for s in top5:
        lines.append(_format_post_line(s, include_eng=True))

    notify("\n".join(lines))
    MONTHLY_STATE.parent.mkdir(parents=True, exist_ok=True)
    MONTHLY_STATE.write_text(month_key, encoding="utf-8")
    return True
