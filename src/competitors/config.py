"""Loader do config/competitors.yml."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config" / "competitors.yml"


def load_competitors() -> list[dict]:
    """Lê competitors.yml. Retorna lista de dicts; [] se arquivo ausente/inválido."""
    if not CONFIG_PATH.exists():
        print(f"⚠ competitors · config ausente em {CONFIG_PATH}")
        return []
    try:
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        print(f"⚠ competitors · YAML parse erro: {e}")
        return []
    items = data.get("competitors", [])
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "handle": (it.get("handle") or "").strip().lstrip("@"),
                "site": (it.get("site") or "").strip().rstrip("/"),
                "notes": (it.get("notes") or "").strip(),
            }
        )
    return out
