"""
Antirepeat store — varre content/briefs/ + content/calendar.csv pra evitar
repetir tópico/template/headline em janela de 60d.

Heurística simples (sem embeddings):
  - keywords extraídas do title, headline, lead
  - tema (do calendar.csv)
  - template
  - publish/scheduled date
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BRIEFS_DIR = ROOT / "content" / "briefs"
CALENDAR = ROOT / "content" / "calendar.csv"

STOPWORDS = {
    "a", "o", "os", "as", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "no", "na", "nos", "nas",
    "em", "ao", "aos", "à", "às", "para", "por", "com", "sem",
    "que", "se", "e", "ou", "mas", "como", "sobre", "ser", "é",
    "ja", "já", "mais", "menos", "muito", "pouco", "tudo", "nada",
    "the", "of", "and", "to", "for", "in", "on", "at", "by", "with",
    "is", "are", "was", "were", "be", "been", "being",
}


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def keywords(text: str, *, min_len: int = 4) -> set[str]:
    text = _normalize(_strip_html(text))
    tokens = re.findall(r"[a-z0-9]+", text)
    return {t for t in tokens if len(t) >= min_len and t not in STOPWORDS}


def _calendar_index() -> dict[str, dict]:
    """Mapeia post_id → linha do calendar.csv (último scheduled_at vence)."""
    if not CALENDAR.exists():
        return {}
    out: dict[str, dict] = {}
    with CALENDAR.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = (row.get("post_id") or "").strip()
            if pid:
                out[pid] = row
    return out


def list_recent_briefs(window_days: int = 60) -> list[dict]:
    """Briefs cujos slots estão dentro da janela (passado E futuro próximo)."""
    if not BRIEFS_DIR.exists():
        return []
    cutoff_past = datetime.now() - timedelta(days=window_days)
    cutoff_future = datetime.now() + timedelta(days=window_days)
    cal = _calendar_index()
    items: list[dict] = []
    for path in sorted(BRIEFS_DIR.glob("*.json")):
        try:
            brief = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        bid = brief.get("id", "")
        # Tenta achar scheduled_at via id base (ex: 38_rpe → 38)
        base = bid.split("_", 1)[0]
        cal_row = cal.get(base) or cal.get(bid)
        if cal_row:
            try:
                scheduled = datetime.strptime(
                    cal_row["scheduled_at"], "%Y-%m-%d %H:%M"
                )
            except (KeyError, ValueError):
                scheduled = None
        else:
            scheduled = None
        if scheduled is None:
            # Sem agendamento, considera por mtime
            scheduled = datetime.fromtimestamp(path.stat().st_mtime)
        if not (cutoff_past <= scheduled <= cutoff_future):
            continue
        items.append(
            {
                "id": bid,
                "template": brief.get("template", ""),
                "pillar": brief.get("pillar", ""),
                "title": brief.get("title", ""),
                "vars": brief.get("vars", {}),
                "scheduled_at": scheduled.isoformat(),
                "theme": (cal_row or {}).get("theme", ""),
                "_keywords": keywords(
                    " ".join(
                        [
                            brief.get("title", ""),
                            brief.get("vars", {}).get("HEADLINE", ""),
                            brief.get("vars", {}).get("LEAD", ""),
                            brief.get("pillar", ""),
                        ]
                    )
                ),
            }
        )
    return items


def redundancy_score(candidate_text: str, recent: list[dict]) -> tuple[float, list[str]]:
    """
    0.0 = único, 1.0 = idêntico a algum recente.
    Retorna (score, ids dos mais próximos).
    """
    cand_kw = keywords(candidate_text)
    if not cand_kw:
        return 0.0, []
    best = 0.0
    similar: list[str] = []
    for item in recent:
        their = item.get("_keywords") or set()
        if not their:
            continue
        inter = len(cand_kw & their)
        union = len(cand_kw | their)
        if union == 0:
            continue
        jaccard = inter / union
        if jaccard >= 0.45:
            similar.append(item["id"])
        if jaccard > best:
            best = jaccard
    return best, similar


def used_themes(recent: list[dict], days: int = 7) -> dict[str, int]:
    """Conta tema dos últimos N dias (anti-monotonia semanal)."""
    cutoff = datetime.now() - timedelta(days=days)
    counts: dict[str, int] = {}
    for it in recent:
        try:
            when = datetime.fromisoformat(it["scheduled_at"])
        except (KeyError, ValueError):
            continue
        if when < cutoff:
            continue
        t = (it.get("theme") or "").strip() or "?"
        counts[t] = counts.get(t, 0) + 1
    return counts


def used_templates(recent: list[dict], days: int = 5) -> dict[str, int]:
    cutoff = datetime.now() - timedelta(days=days)
    counts: dict[str, int] = {}
    for it in recent:
        try:
            when = datetime.fromisoformat(it["scheduled_at"])
        except (KeyError, ValueError):
            continue
        if when < cutoff:
            continue
        t = (it.get("template") or "").strip() or "?"
        counts[t] = counts.get(t, 0) + 1
    return counts
