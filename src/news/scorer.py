"""
Scorer de notícias — usa Claude (sonnet, fast) pra classificar relevância.

Output esperado:
  {
    "score": 0-10 (overall, ponderado internamente pelo modelo),
    "post_event": bool (true se é resultado/ranking/recorde de evento finalizado),
    "viral_potential": 0-10,
    "alignment": 0-10 (com posicionamento Merge),
    "reasoning": "1-2 frases",
    "angle_suggestion": "ângulo sugerido pra um post"
  }
"""
from __future__ import annotations

from llm import complete_json

SYSTEM = """Você é o editor-chefe da Merge — marca de wellness focada em endurance: UTMB, corrida (road/trail), ciclismo (road/MTB), Hyrox, natação. Posicionamento: data-driven, anti bro-science, comunidade técnica de amadores e elite.

Avalie se uma notícia merece virar conteúdo da Merge. Considere:

1. ADERÊNCIA AO NICHO (peso 35%): a notícia trata de UMA das modalidades-foco OU de ciência/performance/recovery/sono/longevidade aplicável a atletas amadores e elite?
2. NOVIDADE (peso 20%): é informação nova/quebra-padrão? Ou é trivial/repetida?
3. POTENCIAL VIRAL (peso 20%): tem hook claro pra arte clickbait? Polêmica? Recorde? Atleta-ícone?
4. ALINHAMENTO POSICIONAMENTO MERGE (peso 25%): combina com tom de comunidade séria, dados, ciência? OU é wellness pop sem substância?

GATILHOS PÓS-EVENTO (post_event=true) merecem score alto se:
- Ranking final de evento de grande renome (UTMB, Berlin, Boston, Tour, Ironman, Hyrox)
- Recorde quebrado
- Polêmica/decisão arbitragem com impacto na corrida
- Lesão/abandono de atleta-âncora
- Resultado-surpresa (azarão venceu)

NÃO ache score alto pra:
- Anúncio de patrocínio sem novidade técnica
- Lançamento comercial puro de tênis/wearable sem dado novo
- Lifestyle puro sem ciência

Score >= 8 → post reativo de feed (URGENTE, max 1h)
Score 5-7 → vai pra pool (stories ou planner usa)
Score < 5 → ignora
"""


def score_item(item: dict) -> dict:
    user = (
        f"Notícia:\n"
        f"FONTE: {item.get('feed_name','?')} ({item.get('category','?')})\n"
        f"TÍTULO: {item.get('title','?')}\n"
        f"RESUMO: {item.get('summary','')[:600]}\n"
        f"PUBLICADO: {item.get('published_at','?')}\n"
        f"MODALIDADES (do feed): {', '.join(item.get('modalities', []))}\n"
        f"\nDevolva JSON:\n"
        '{\n'
        '  "score": 0-10 (float ok),\n'
        '  "post_event": true|false,\n'
        '  "viral_potential": 0-10,\n'
        '  "alignment": 0-10,\n'
        '  "primary_modality": "<modalidade que melhor categoriza, em snake_case>",\n'
        '  "reasoning": "<1-2 frases curtas pt-BR>",\n'
        '  "angle_suggestion": "<ângulo pra um post; pt-BR; max 120 chars>"\n'
        '}'
    )
    result = complete_json(
        system=SYSTEM,
        user=user,
        fast=True,  # volume alto, scoring é quase um classificador
        max_tokens=600,
        temperature=0.3,
    )
    if not isinstance(result, dict):
        return {"score": 0.0, "post_event": False, "reasoning": "scorer falhou"}
    # sanity
    try:
        result["score"] = float(result.get("score", 0))
    except (TypeError, ValueError):
        result["score"] = 0.0
    result["post_event"] = bool(result.get("post_event", False))
    return result
