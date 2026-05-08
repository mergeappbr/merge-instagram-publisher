"""
Reviewer — checa brief antes de mandar pro Telegram. NÃO bloqueia: apenas
sinaliza issues que o preview vai listar pra Pedro decidir.

Checks:
  1. Tom (linguagem proibida: emoji, "você sabia", "vamos falar", "imagina só")
  2. Claim médico arriscado ("cura", "garante", "100%", "elimina")
  3. Redundância >0.45 jaccard com últimos 60d (via store)
  4. Headline tem <span class="hl">
  5. Caption tem hook na 1ª linha (não começa com "olá", "fala", etc)
"""
from __future__ import annotations

import re
from typing import Any

from . import store

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\U0001F600-\U0001F64F"
    "\U00002700-\U000027BF"
    "\U0001FA00-\U0001FAFF"
    "]"
)

PROIBIDAS = [
    r"\bvocê sabia\b",
    r"\bvamos falar\b",
    r"\bimagina só\b",
    r"\bquerido leitor\b",
    r"\bolá pessoal\b",
    r"\bfala galera\b",
    # CTAs de "story bait" — Merge não oferece esses serviços (sem link nos
    # destaques, sem swipe up, sem link na bio direcionando pra matéria).
    # News sempre aponta pro próprio post no feed.
    r"\blink nos destaques\b",
    r"\blink no destaque\b",
    r"\bnos destaques\b",
    r"\blink na bio\b",
    r"\bswipe up\b",
    r"\barrasta pra cima\b",
    r"\barraste pra cima\b",
]

CLAIM_RISCO = [
    r"\bcura\b",
    r"\bgarante\b",
    r"\b100%\s*efic",
    r"\belimina\s+(dor|lesão|cansaço)",
    r"\bsem efeito colateral\b",
]


def review(brief: dict) -> dict[str, Any]:
    """Retorna {ok: bool, warnings: [str], blockers: [str], redundancy: float}"""
    warnings: list[str] = []
    blockers: list[str] = []

    vars_ = brief.get("vars", {})
    story = brief.get("story_vars", {})
    caption = brief.get("caption_md", "")

    haystack = " ".join(
        [
            vars_.get("HEADLINE", ""),
            vars_.get("LEAD", ""),
            vars_.get("PILL", ""),
            story.get("HEADLINE", ""),
            story.get("LEAD", ""),
            caption,
        ]
    )

    if EMOJI_RE.search(haystack):
        warnings.append("contém emoji (proibido em arte/legenda).")

    for pat in PROIBIDAS:
        if re.search(pat, haystack, flags=re.IGNORECASE):
            warnings.append(f"linguagem proibida: <code>{pat}</code>")

    for pat in CLAIM_RISCO:
        if re.search(pat, haystack, flags=re.IGNORECASE):
            blockers.append(f"claim médico arriscado: <code>{pat}</code>")

    if "<span" not in vars_.get("HEADLINE", ""):
        warnings.append("HEADLINE sem destaque <code>&lt;span class=\"hl\"&gt;</code>.")

    cap_first = (caption.strip().splitlines() or [""])[0].lower()
    weak_starts = ("olá", "oi pessoal", "fala galera", "bom dia", "boa tarde")
    if cap_first.startswith(weak_starts):
        warnings.append("caption começa com saudação fraca; prefira lead direto.")

    # redundância
    candidate_text = " ".join(
        [
            brief.get("title", ""),
            vars_.get("HEADLINE", ""),
            vars_.get("LEAD", ""),
            brief.get("pillar", ""),
        ]
    )
    recent = store.list_recent_briefs(window_days=60)
    score, similar = store.redundancy_score(candidate_text, recent)
    if score >= 0.6:
        blockers.append(f"redundância alta ({score:.2f}) com: {', '.join(similar[:3])}")
    elif score >= 0.4:
        warnings.append(f"redundância média ({score:.2f}) com: {', '.join(similar[:3])}")

    return {
        "ok": not blockers,
        "warnings": warnings,
        "blockers": blockers,
        "redundancy": score,
    }
