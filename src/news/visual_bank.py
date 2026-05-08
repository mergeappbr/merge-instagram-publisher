"""Lookup local de imagens REAIS antes de cair em IA.

Duas fontes:
  1. brand/images/*.{jpg,png,webp,jpeg} — curated por Pedro (produtos
     específicos, fotos oficiais de eventos: AdiosPro4, MaratonaRio,
     MaratonaPOA, Sertoes, IronMan, EvoSL, Camisetas).
  2. brand/images/_bank/<category>/{unsplash,pexels}/*.jpg — pré-baixado
     via image_bank.py, categorizado por slug (marathon_finish_line,
     trail_running_effort, swimmer_pool, track_sprint, etc).

Filosofia: foto real é SEMPRE melhor que IA. Bank-first; IA só é último
recurso quando nada bate. Isso elimina cara-de-IA, mãos esquisitas,
proporções erradas.
"""
from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
CURATED_DIR = ROOT / "brand" / "images"
BANK_DIR = CURATED_DIR / "_bank"

# Banco pessoal Diogo Villarinho (atleta BR triatleta) — local-only.
# Filenames genéricos (DSC*/G0*/IMG_*), sem metadado, então usado só como
# fallback aleatório pra modalidades cobertas (swim/triathlon/ironman).
# Em Railway esse path não existe → retorna None silenciosamente.
DIOGO_BANK_DIR = Path.home() / "Desktop" / "Diogo Villarinho - 2025"
DIOGO_MODALITIES = {"swimming", "triathlon", "ironman", "open water", "natacao", "natação"}

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Keywords no título/summary → keywords no filename curated.
# Match é substring lowercase nos dois lados.
CURATED_KEYWORDS = {
    # — Produtos —
    "adios pro 4":          ["adiospro4"],
    "adios pro 5":          ["adiospro5"],
    "adiospro":             ["adiospro"],
    "adidas":               ["adios", "evosl", "camisetamaratonario"],
    "evo sl":               ["evosl"],
    # — Eventos —
    "maratona do rio":      ["maratonario"],
    "maratona rio":         ["maratonario"],
    "rio marathon":         ["maratonario"],
    "maratona porto alegre": ["maratonapoa"],
    "maratona poa":         ["maratonapoa"],
    "sertões":              ["sertoes"],
    "sertoes":              ["sertoes"],
    "ironman":              ["ironman"],
    "70.3":                 ["ironman"],
    "triatlo":              ["ironman"],
    "triathlon":            ["ironman"],
    # — Misc —
    "garmin":               ["garmin", "fitbit"],
    "fitbit":               ["fitbit"],
    "google watch":         ["fitbit"],
}

# Modalidade + cena → categoria do _bank.
# Lista de tuplas (modalidade_substring, scene_keywords, bank_category).
# modalidade_substring=None significa qualquer modalidade.
BANK_CATEGORY_MAP: list[tuple[Optional[str], list[str], str]] = [
    ("running",    ["finish", "chegada", "linha de chegada", "podium", "winner"],
                                                              "marathon_finish_line"),
    ("running",    ["maratona", "marathon", "42k", "21k", "half marathon"],
                                                              "marathon_finish_line"),
    ("running",    ["trail", "trilha", "ultra", "ultratrail", "skyrun"],
                                                              "trail_running_effort"),
    ("trail",      [],                                        "trail_running_effort"),
    ("running",    ["lesão", "lesao", "injury", "fisio", "physio", "tendinite",
                    "fratura", "stress fracture"],            "running_injury_physiotherapy"),
    ("running",    ["track", "pista", "sprint", "100m", "200m", "400m", "800m",
                    "1500m", "5000m", "10000m"],              "track_sprint_athlete"),
    ("triathlon",  ["swim", "natação", "natacao", "open water", "piscina"],
                                                              "swimmer_pool_training"),
    ("swimming",   [],                                        "swimmer_pool_training"),
    (None,         ["forerunner", "fenix", "garmin", "watch", "smartwatch",
                    "relógio", "relogio", "wearable", "fr965", "epix"],
                                                              "garmin_fr965"),
    (None,         ["wearable", "smartwatch", "wrist", "pulso"],
                                                              "smartwatch_running_watch"),
]


def _list_curated() -> list[Path]:
    if not CURATED_DIR.is_dir():
        return []
    return [
        f for f in CURATED_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in IMG_EXTS
    ]


def _list_bank_category(category: str) -> list[Path]:
    cat_dir = BANK_DIR / category
    if not cat_dir.is_dir():
        return []
    out: list[Path] = []
    for sub in ("unsplash", "pexels"):
        d = cat_dir / sub
        if d.is_dir():
            out.extend(
                f for f in d.iterdir()
                if f.is_file() and f.suffix.lower() in IMG_EXTS
            )
    return out


def _lookup_curated(
    title: str, summary: str, product_model: Optional[str] = None
) -> Optional[Path]:
    text = f"{title} {summary}".lower()
    keywords_hit: list[str] = []
    for trigger, file_keys in CURATED_KEYWORDS.items():
        if trigger in text:
            keywords_hit.extend(file_keys)
    if product_model:
        # "Adios Pro 4" → "adiospro4"
        slug = re.sub(r"\s+", "", product_model.lower())
        keywords_hit.append(slug)
    if not keywords_hit:
        return None
    files = _list_curated()
    matches: list[Path] = []
    for f in files:
        name_lower = f.name.lower()
        if any(k in name_lower for k in keywords_hit):
            matches.append(f)
    if not matches:
        return None
    # Aleatório dentro dos matches pra variar entre posts
    return random.choice(matches)


def _lookup_bank(modality: str, title: str, summary: str) -> Optional[tuple[Path, str]]:
    text = f"{title} {summary}".lower()
    mod_lower = (modality or "").lower()
    for mod_match, scene_kws, cat in BANK_CATEGORY_MAP:
        if mod_match and mod_match not in mod_lower:
            continue
        if scene_kws and not any(kw in text for kw in scene_kws):
            continue
        candidates = _list_bank_category(cat)
        if candidates:
            return random.choice(candidates), cat
    return None


def _list_diogo_bank() -> list[Path]:
    """Lista fotos do banco pessoal Diogo (root + Fotos Extras, recursivo
    leve). Skip subpastas que claramente não são fotos do atleta
    ('merge-mobile-screens-v2' contém screenshots do app)."""
    if not DIOGO_BANK_DIR.is_dir():
        return []
    SKIP_DIRS = {"merge-mobile-screens-v2"}
    out: list[Path] = []
    for f in DIOGO_BANK_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in IMG_EXTS:
            out.append(f)
        elif f.is_dir() and f.name not in SKIP_DIRS:
            for sub in f.iterdir():
                if sub.is_file() and sub.suffix.lower() in IMG_EXTS:
                    out.append(sub)
    return out


def _lookup_diogo(modality: str, title: str, summary: str) -> Optional[Path]:
    """Fallback aleatório do banco pessoal pra modalidades cobertas.
    Só dispara se modalidade bate (swim/triathlon/ironman). Filenames
    são genéricos, então é roleta — mas é foto REAL, alta qualidade."""
    mod_lower = (modality or "").lower()
    text = f"{title} {summary}".lower()
    matches_modality = (
        any(m in mod_lower for m in DIOGO_MODALITIES)
        or any(m in text for m in DIOGO_MODALITIES)
    )
    if not matches_modality:
        return None
    files = _list_diogo_bank()
    if not files:
        return None
    return random.choice(files)


def lookup(
    title: str,
    summary: str,
    modality: str,
    product_model: Optional[str] = None,
) -> Optional[tuple[Path, str]]:
    """Tenta achar foto REAL pra essa notícia.

    Retorna (caminho_local, label_pra_log) ou None.
    label: 'curated:<filename>' / 'bank:<cat>:<filename>' / 'diogo:<filename>'.
    """
    # Camada 1: curated (fotos oficiais com nome descritivo)
    p = _lookup_curated(title, summary, product_model)
    if p:
        return p, f"curated:{p.name}"
    # Camada 2: bank por categoria (Unsplash/Pexels pré-baixado)
    hit = _lookup_bank(modality, title, summary)
    if hit:
        path, cat = hit
        return path, f"bank:{cat}:{path.name}"
    # Camada 3: Diogo Villarinho (local-only, modalidades natação/triatlo)
    p = _lookup_diogo(modality, title, summary)
    if p:
        return p, f"diogo:{p.name}"
    return None
