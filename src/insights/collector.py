"""
Coleta diária de insights da Graph API pra cada media_id em published.csv.

Janela: posts publicados nos últimos 30 dias (insights estabilizam em ~7d, mas
mantemos 30 pra capturar long-tail de saves/shares).

Snapshots vão pra output/insights.csv (append-only, 1 linha por (post, dia)).
Não sobrescreve histórico — permite plotar evolução posteriormente.

Métricas coletadas (Graph API v21+):
  views, reach, saved, shares, likes, comments, total_interactions
  (reels também: plays — alias de views em alguns retornos)

Falha de rede ou métrica ausente: log + continua. Nunca derruba o scheduler.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
PUBLISHED = ROOT / "output" / "published.csv"
INSIGHTS = ROOT / "output" / "insights.csv"

GRAPH_BASE = "https://graph.facebook.com/v21.0"
TOKEN = os.getenv("META_GRAPH_ACCESS_TOKEN", "").strip()

WINDOW_DAYS = 30
TIMEOUT = 30.0

METRICS_DEFAULT = "views,reach,saved,shares,likes,comments,total_interactions"

INSIGHTS_FIELDS = [
    "snapshot_date",
    "media_id",
    "post_id",
    "format",
    "age_days",
    "views",
    "reach",
    "saved",
    "shares",
    "likes",
    "comments",
    "total_interactions",
    "fetched_at",
]


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_recent_published(window_days: int = WINDOW_DAYS) -> list[dict]:
    """Posts publicados em <= window_days, com media_id real (não vazio)."""
    if not PUBLISHED.exists():
        return []
    cutoff = datetime.now() - timedelta(days=window_days)
    rows: list[dict] = []
    with PUBLISHED.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = (row.get("media_id") or "").strip()
            if not mid:
                continue  # agendados ainda não publicaram
            ts = _parse_iso(row.get("timestamp") or "")
            if ts is None or ts < cutoff:
                continue
            rows.append({**row, "_published_at": ts})
    return rows


def fetch_insights(media_id: str) -> dict[str, int]:
    """Pega métricas pra 1 media_id. Retorna dict (vazio se falhar)."""
    if not TOKEN:
        return {}
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/{media_id}/insights",
            params={"metric": METRICS_DEFAULT, "access_token": TOKEN},
            timeout=TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ insights fetch erro {media_id}: {e!r}")
        return {}
    if r.status_code != 200:
        # 400 com "metric not supported" pra alguns formatos antigos: tenta menos.
        if r.status_code == 400 and "does not support" in r.text.lower():
            return _fetch_insights_minimal(media_id)
        print(f"⚠ insights {r.status_code} em {media_id}: {r.text[:200]}")
        return {}
    out: dict[str, int] = {}
    for entry in r.json().get("data", []):
        name = entry.get("name", "")
        try:
            out[name] = int(entry["values"][0]["value"])
        except (KeyError, IndexError, ValueError, TypeError):
            out[name] = 0
    return out


def _fetch_insights_minimal(media_id: str) -> dict[str, int]:
    """Fallback pra mídia que rejeita algum metric (ex: posts < v21)."""
    minimal = "reach,likes,comments,saved,shares"
    try:
        r = httpx.get(
            f"{GRAPH_BASE}/{media_id}/insights",
            params={"metric": minimal, "access_token": TOKEN},
            timeout=TIMEOUT,
        )
    except Exception:  # noqa: BLE001
        return {}
    if r.status_code != 200:
        return {}
    out: dict[str, int] = {}
    for entry in r.json().get("data", []):
        try:
            out[entry["name"]] = int(entry["values"][0]["value"])
        except (KeyError, IndexError, ValueError, TypeError):
            continue
    return out


def append_snapshot(
    snap_date: str,
    row: dict,
    metrics: dict[str, int],
    age_days: int,
) -> None:
    INSIGHTS.parent.mkdir(parents=True, exist_ok=True)
    is_new = not INSIGHTS.exists()
    with INSIGHTS.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INSIGHTS_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(
            {
                "snapshot_date": snap_date,
                "media_id": row["media_id"],
                "post_id": row.get("post_id", ""),
                "format": row.get("format", ""),
                "age_days": age_days,
                "views": metrics.get("views", 0),
                "reach": metrics.get("reach", 0),
                "saved": metrics.get("saved", 0),
                "shares": metrics.get("shares", 0),
                "likes": metrics.get("likes", 0),
                "comments": metrics.get("comments", 0),
                "total_interactions": metrics.get("total_interactions", 0),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        )


def collect_now() -> int:
    """Snapshot atual de todos posts <30d. Retorna nº de coletados ok."""
    if not TOKEN:
        return 0
    snap_date = datetime.now().strftime("%Y-%m-%d")
    posts = load_recent_published()
    if not posts:
        return 0
    n_ok = 0
    for row in posts:
        age = (datetime.now() - row["_published_at"]).days
        m = fetch_insights(row["media_id"])
        if not m:
            continue
        append_snapshot(snap_date, row, m, age)
        n_ok += 1
    print(f"insights · {n_ok}/{len(posts)} coletados em {snap_date}")
    return n_ok
