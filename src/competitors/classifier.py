"""
Classificação de temas recorrentes via Claude Sonnet.

Recebe lista bruta (captions IG + headlines de blog) e devolve clusters:
  [{"tema": "...", "frequencia": N, "exemplo": "..."}]

Usa fast=True (sonnet) — é classificação leve em volume.
"""
from __future__ import annotations

from llm import complete_json

SYSTEM = """Você é analista de inteligência competitiva da Merge — marca BR de wellness focada em endurance (UTMB, corrida road/trail, ciclismo road/MTB, Hyrox, natação).

Recebe textos curtos (captions de Instagram + headlines de blog) de UM concorrente na última semana e classifica em 3-6 TEMAS RECORRENTES.

Tema deve ser:
- Curto (max 4 palavras), em pt-BR, descrevendo o ÂNGULO editorial (não só a modalidade).
- Útil pra Merge entender posicionamento do concorrente.

Exemplos de bons temas:
- "lançamento tênis road"
- "story de atleta amador"
- "review de wearable"
- "treino HIIT iniciante"
- "evento de marca / corrida fechada"
- "ciência do sono"

NÃO classifique como simplesmente "corrida" ou "ciclismo" — o ângulo importa mais que a modalidade.
"""


def cluster_themes(texts: list[str], competitor_name: str) -> list[dict]:
    """Devolve [{"tema": str, "frequencia": int, "exemplo": str}, ...]. [] se falhar."""
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return []
    # cap de tamanho pra não estourar tokens
    sample = cleaned[:40]
    joined = "\n".join(f"- {t[:240]}" for t in sample)

    user = (
        f"Concorrente: {competitor_name}\n"
        f"Posts/headlines da última semana ({len(sample)} itens):\n"
        f"{joined}\n\n"
        "Devolva JSON:\n"
        "{\n"
        '  "temas": [\n'
        '    {"tema": "...", "frequencia": <int>, "exemplo": "<headline curto>"},\n'
        "    ...\n"
        "  ]\n"
        "}\n"
        "Máx 6 temas. Ordene por frequencia desc."
    )

    try:
        result = complete_json(
            system=SYSTEM,
            user=user,
            fast=True,
            max_tokens=800,
            temperature=0.3,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ competitors/classifier · {competitor_name} falhou: {e!r}")
        return []

    if not isinstance(result, dict):
        return []
    temas = result.get("temas", [])
    if not isinstance(temas, list):
        return []
    out: list[dict] = []
    for t in temas[:6]:
        if not isinstance(t, dict):
            continue
        nome = (t.get("tema") or "").strip()
        if not nome:
            continue
        try:
            freq = int(t.get("frequencia", 1))
        except (TypeError, ValueError):
            freq = 1
        out.append(
            {
                "tema": nome[:60],
                "frequencia": freq,
                "exemplo": (t.get("exemplo") or "").strip()[:160],
            }
        )
    return out
