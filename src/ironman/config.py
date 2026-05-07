"""
Loader de config/races.yml.

Schema de cada race:
  id: str (slug curto, vira prefixo dos brief_ids gerados)
  name: str
  kind: ironman | mtb | trail
  distance: full | 70.3        # só pra kind=ironman
  location: str                 # "Florianópolis · SC"
  location_short: str           # "FLORIPA"
  date: YYYY-MM-DD              # primeiro dia da prova
  date_end: YYYY-MM-DD          # opcional, pra provas multi-dia
  site: url
  ironstats_handle: str         # opcional, IG pra cross-validation de resultados
  logo: filename                # arquivo em brand/ (não brand/images/)
  bg_countdown: filename        # em brand/images/
  bg_results_cover: filename
  bg_results_male: filename
  bg_results_female: filename
  race_label_short: str         # ex "FLORIPA · 31/05" — vai no footer
  kicker: str                   # ex "IRONMAN BRASIL · 31/05/26" — pill superior
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "races.yml"


def load_races() -> list[dict]:
    if not CONFIG_PATH.exists():
        return []
    try:
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"⚠ races.yml parse error: {e!r}")
        return []
    if not isinstance(data, dict):
        return []
    races = data.get("races") or []
    out: list[dict] = []
    for r in races:
        if not isinstance(r, dict):
            continue
        if not r.get("id") or not r.get("date"):
            continue
        out.append(r)
    return out


def parse_race_date(value: str) -> date:
    """'2026-05-31' → date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def days_until(race: dict, today: date) -> int:
    """Dias até a prova. Negativo se já aconteceu."""
    return (parse_race_date(race["date"]) - today).days


def days_after(race: dict, today: date) -> int:
    """Dias DESDE a prova (último dia se for multi-dia). Negativo se ainda vai acontecer."""
    end_str = race.get("date_end") or race["date"]
    return (today - parse_race_date(end_str)).days
