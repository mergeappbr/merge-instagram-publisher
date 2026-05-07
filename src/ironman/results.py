"""
Coleta + cross-validação de resultados (top 10 M+F) pós-prova.

Estratégia em camadas (best-effort, falha gracefully):
  1. fetch_from_ironstats(handle) — pega últimos posts via Business Discovery API,
     usa Claude com vision pra OCR/parse das imagens de ranking
  2. fetch_from_ironman_official(race) — tenta o endpoint público de resultados
     (ironman.com/im-X-results) e parseia a tabela
  3. cross_validate(a, b) — confere se top 3 batem; warning se discordam
  4. fallback: Pedro provê manualmente via free-text "ajuste" no Telegram

Por enquanto (v1): fetchers são skeleton; quando Pedro recebe o aviso T+1 sem
dados confirmados, ele responde com texto formatado e a runner.py parseia.

Formato esperado do retorno:
{
  "male":   [{"pos": 1, "name": "Igor Amorelli", "time": "08:23:11"}, ...10],
  "female": [{"pos": 1, "name": "Luisa Baptista", "time": "09:18:44"}, ...10],
  "source": "ironstats" | "ironman_official" | "manual",
  "verified": True | False,
  "warnings": ["..."],
}
"""
from __future__ import annotations

import re

# Pattern aceito pra entrada manual do Pedro:
#   linha: "1. Igor Amorelli 08:23:11"
#   ou "1 Igor Amorelli 08:23:11"
#   ou "1) Igor Amorelli — 08:23:11"
# Aceita seções "MASCULINO:" e "FEMININO:" pra separar.
ROW_RE = re.compile(
    r"^\s*(\d{1,2})[\.\)\-\s]+([A-Za-zÀ-ÿ\s\.\-']+?)[\s\-—–]+(\d{1,2}:\d{2}:\d{2})\s*$"
)
SECTION_RE = re.compile(r"^\s*(masculino|feminino|m|f)\s*:?\s*$", re.IGNORECASE)


def fetch_from_ironstats(handle: str) -> dict | None:
    """Stub: lê últimos posts do @handle via Business Discovery e tenta parsear
    rankings via Claude vision. Não implementado em v1 — retorna None."""
    return None


def fetch_from_ironman_official(race: dict) -> dict | None:
    """Stub: scrape ironman.com/<slug>-results. Não implementado em v1 — retorna None."""
    return None


def cross_validate(a: dict | None, b: dict | None) -> tuple[dict | None, list[str]]:
    """Compara duas fontes. Se top 3 batem, marca verified=True. Senão, warnings."""
    warnings: list[str] = []
    if a is None and b is None:
        return None, ["nenhuma fonte respondeu"]
    if a is None:
        return b, ["só fonte secundária respondeu"]
    if b is None:
        return a, ["só fonte primária respondeu"]
    out = dict(a)
    out["verified"] = True
    for gender in ("male", "female"):
        ta = (a.get(gender) or [])[:3]
        tb = (b.get(gender) or [])[:3]
        for ra, rb in zip(ta, tb):
            if (ra.get("name") or "").lower() != (rb.get("name") or "").lower():
                warnings.append(
                    f"{gender} top {ra.get('pos')}: {ra.get('name')!r} vs {rb.get('name')!r}"
                )
                out["verified"] = False
    out["warnings"] = warnings
    return out, warnings


def parse_manual(text: str) -> dict | None:
    """Parse de entrada livre. Espera dois blocos (MASCULINO / FEMININO),
    cada um com 10 linhas no formato "POS NOME TEMPO".

    Tolerante a variações; ignora linhas que não casam o padrão.
    Retorna None se não houver pelo menos 1 corredor por gênero.
    """
    current = None
    male: list[dict] = []
    female: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sec = SECTION_RE.match(line)
        if sec:
            tag = sec.group(1).lower()
            current = "male" if tag in ("masculino", "m") else "female"
            continue
        m = ROW_RE.match(line)
        if not m:
            continue
        pos = int(m.group(1))
        name = m.group(2).strip()
        time_ = m.group(3)
        row = {"pos": pos, "name": name, "time": time_}
        if current == "female":
            female.append(row)
        else:
            male.append(row)
    if not male and not female:
        return None
    return {
        "male": male[:10],
        "female": female[:10],
        "source": "manual",
        "verified": False,
        "warnings": ["entrada manual — confira nomes e tempos antes de aprovar"],
    }


def try_fetch(race: dict) -> dict | None:
    """Tenta as duas fontes e cross-valida. Retorna None se ambas falharem."""
    handle = race.get("ironstats_handle") or ""
    a = fetch_from_ironstats(handle) if handle else None
    b = fetch_from_ironman_official(race)
    merged, _ = cross_validate(a, b)
    return merged
