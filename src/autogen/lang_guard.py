"""Language guard: detecta brief em inglês e traduz pra pt-BR via Gemini.

Problema: o writer prompt manda "TRADUZA TUDO pra pt-BR", mas o LLM
ocasionalmente devolve trechos em inglês (especialmente quando a fonte
é inglesa — Outside, Velo, Runner's World). O guard roda DEPOIS do
writer e ANTES do reviewer, garantindo que vars.HEADLINE / vars.LEAD /
caption_md / story_vars.* estejam em pt-BR.

Heurística cheap (sem custo): conta razão de stopwords inglesas vs
totais. Se ≥ 0.08, marca como inglês.

Quando marcado como inglês, chama Gemini Flash com prompt explícito de
tradução preservando: HTML tags (<span class="hl">, <br>), nomes
próprios (atletas, marcas, provas) e termos técnicos sem tradução
boa (pace, VO2max, split, taper).

Falha silenciosa: se Gemini falhar, devolve brief original com warning
no campo `_lang_debug`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)

# Stopwords inglesas comuns que NÃO existem em pt-BR (filtra ruído de
# nomes próprios). 'a' e 'no' excluídos (ambíguos com pt-BR).
_EN_STOPWORDS = frozenset({
    "the", "of", "and", "is", "for", "to", "with", "on", "in", "at",
    "from", "by", "this", "that", "are", "was", "were", "will", "would",
    "have", "has", "had", "been", "being", "their", "his", "her", "its",
    "they", "them", "you", "your", "our", "but", "not", "all", "any",
    "what", "which", "when", "where", "while", "after", "before",
    "between", "into", "during", "through", "about", "against", "than",
    "such", "only", "also", "more", "most", "some", "many", "much",
    "according", "reported", "however", "although",
})

# Fields no brief que precisam estar em pt-BR.
_TEXT_FIELDS_IN_VARS = ("HEADLINE", "LEAD", "PILL")
_BRIEF_TEXT_KEY = "caption_md"

_WORD_RE = re.compile(r"\b[a-zA-Z]+\b")


def _english_ratio(text: str) -> float:
    """Razão de stopwords inglesas / palavras totais. 0.0 se vazio."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    if len(words) < 10:
        return 0.0
    hits = sum(1 for w in words if w in _EN_STOPWORDS)
    return hits / len(words)


def _collect_text(brief: dict) -> str:
    """Junta todos os campos textuais do brief pra análise."""
    chunks: list[str] = []
    vars_ = brief.get("vars") or {}
    for k in _TEXT_FIELDS_IN_VARS:
        v = vars_.get(k)
        if isinstance(v, str):
            chunks.append(v)
    cap = brief.get(_BRIEF_TEXT_KEY)
    if isinstance(cap, str):
        chunks.append(cap)
    sv = brief.get("story_vars") or {}
    for k in _TEXT_FIELDS_IN_VARS:
        v = sv.get(k)
        if isinstance(v, str):
            chunks.append(v)
    return "\n".join(chunks)


def is_english(brief: dict, threshold: float = 0.08) -> tuple[bool, float]:
    """Retorna (em_ingles, ratio)."""
    text = _collect_text(brief)
    ratio = _english_ratio(text)
    return ratio >= threshold, ratio


def _translate_via_gemini(payload: dict) -> Optional[dict]:
    """Manda payload JSON pro Gemini Flash e devolve JSON traduzido.

    payload = {"headline": "...", "lead": "...", "caption_md": "...",
               "story_headline": "...", "story_lead": "..."}
    Preserva HTML tags e nomes próprios.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    prompt = (
        "Traduza os campos abaixo de INGLÊS pra PORTUGUÊS BRASILEIRO.\n"
        "Devolva APENAS JSON com as MESMAS chaves (sem markdown, sem prosa).\n\n"
        "REGRAS:\n"
        "1. Preserve EXATAMENTE as tags HTML: <span class=\"hl\">...</span>, <br>.\n"
        "2. Mantenha em INGLÊS: nomes próprios (atletas, marcas, provas, lugares),\n"
        "   termos técnicos sem tradução boa (pace, VO2max, split, taper, drop,\n"
        "   threshold, tempo, fartlek, easy, long run).\n"
        "3. Voz Merge: direta, técnica, sem 'De acordo com', 'Foi reportado que'.\n"
        "4. NÃO mude a estrutura (parágrafos, bullets, blank lines).\n"
        "5. Mantenha lowercase nas headlines (estilo Merge).\n"
        "6. NÃO adicione campos novos. NÃO traduza chaves do JSON.\n\n"
        f"INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "OUTPUT (JSON):"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    url = GEMINI_URL_TMPL.format(model=GEMINI_MODEL, key=key)
    backoffs = [3, 10, 25]
    for attempt in range(len(backoffs) + 1):
        try:
            with httpx.Client(timeout=60) as c:
                r = c.post(url, json=body, headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                resp = r.json()
                cands = resp.get("candidates") or []
                if not cands:
                    return None
                parts = cands[0].get("content", {}).get("parts", []) or []
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    s, e = text.find("{"), text.rfind("}")
                    if s >= 0 and e > s:
                        try:
                            return json.loads(text[s : e + 1])
                        except json.JSONDecodeError:
                            return None
                    return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < len(backoffs):
                import time
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ lang_guard.gemini HTTP {r.status_code}: {r.text[:160]}")
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < len(backoffs):
                import time
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ lang_guard.gemini exception: {e!r}")
            return None
    return None


def ensure_portuguese(brief: dict, *, force: bool = False) -> dict:
    """Garante que brief está em pt-BR. Mutates + retorna o mesmo brief.

    Se for=False (default), só roda tradução se _english_ratio ≥ 0.08.
    Se force=True, sempre traduz (útil pra teste).

    Adiciona brief['_lang_debug'] com {detected_ratio, action, ok}.
    """
    if os.environ.get("LANG_GUARD_DISABLED") == "1":
        brief["_lang_debug"] = {"action": "disabled-env"}
        return brief

    is_en, ratio = is_english(brief)
    if not force and not is_en:
        brief["_lang_debug"] = {"detected_ratio": round(ratio, 3), "action": "skip-ok"}
        return brief

    vars_ = brief.get("vars") or {}
    sv = brief.get("story_vars") or {}
    payload = {
        "headline": vars_.get("HEADLINE", ""),
        "lead": vars_.get("LEAD", ""),
        "pill": vars_.get("PILL", ""),
        "caption_md": brief.get("caption_md", ""),
        "story_headline": sv.get("HEADLINE", ""),
        "story_lead": sv.get("LEAD", ""),
        "story_pill": sv.get("PILL", ""),
    }
    translated = _translate_via_gemini(payload)
    if not translated:
        brief["_lang_debug"] = {
            "detected_ratio": round(ratio, 3),
            "action": "translate-failed",
            "ok": False,
        }
        return brief

    # Aplica de volta (só sobrescreve se chave tinha valor original)
    def _put(target: dict, key: str, new_key: str) -> None:
        v = translated.get(new_key)
        if isinstance(v, str) and v.strip() and target.get(key):
            target[key] = v

    _put(vars_, "HEADLINE", "headline")
    _put(vars_, "LEAD", "lead")
    _put(vars_, "PILL", "pill")
    if isinstance(translated.get("caption_md"), str) and translated["caption_md"].strip():
        brief["caption_md"] = translated["caption_md"]
    _put(sv, "HEADLINE", "story_headline")
    _put(sv, "LEAD", "story_lead")
    _put(sv, "PILL", "story_pill")

    # Re-checa ratio pós-tradução
    _, new_ratio = is_english(brief)
    brief["_lang_debug"] = {
        "detected_ratio": round(ratio, 3),
        "post_ratio": round(new_ratio, 3),
        "action": "translated",
        "ok": new_ratio < 0.08,
    }
    return brief


if __name__ == "__main__":
    # Smoke test
    import sys
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    sample = {
        "vars": {
            "HEADLINE": "Roka launches <span class=\"hl\">Maverick II</span> for Ironman",
            "LEAD": "The new wetsuit features Yamamoto 39 SCS neoprene with 5mm torso, the maximum allowed by Ironman regulations.",
            "PILL": "WETSUIT · GEAR",
        },
        "caption_md": (
            "Roka announced today the launch of the Maverick II wetsuit. "
            "According to the brand, the new model uses Yamamoto 39 SCS "
            "neoprene with 5mm torso thickness, which is the maximum allowed "
            "in Ironman and 70.3 races. The price is set at $1,150.\n\n"
            "Key specs:\n- 5mm torso (Ironman legal max)\n- RS2 sealed seams\n- arms-up shoulder construction"
        ),
        "story_vars": {"HEADLINE": "Roka <span class=\"hl\">Maverick II</span> drops", "LEAD": "the new wetsuit is here."},
    }
    print("Before:", is_english(sample))
    out = ensure_portuguese(sample, force=("--force" in sys.argv))
    print(json.dumps(out["_lang_debug"], indent=2))
    print("\n--- TRANSLATED ---")
    print("HEADLINE:", out["vars"]["HEADLINE"])
    print("LEAD:", out["vars"]["LEAD"])
    print("CAPTION:\n", out["caption_md"][:400])
