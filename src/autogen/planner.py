"""
Planner editorial 2.0 — gera plano semanal estruturado via Claude.

Camadas:
  1. Backbone temático rotativo (seg → dom)
  2. Formato por horário (09h leve · 19h denso)
  3. Datas-âncora (eventos UTMB, Berlin, Tour, etc dentro de 30d)
  4. Slot wildcard reactive (1/semana)
  5. Storytelling em série quando há gancho

Devolve JSON list de 14 entries (7 dias × 2 slots) — cada entry é input pro writer:
  { day_label, scheduled_at, slot_hour, theme, format, hook_idea, modality, pillar }

Modalidades-foco: UTMB, corrida (road/trail), ciclismo (road+MTB),
Hyrox, natação. Foco endurance.

Pilares válidos (espelham briefs existentes):
  educacional-{modalidade}, modalidade-{modalidade}, ciencia-performance,
  produto-merge, comunidade, evento, news-reativo, recovery, nutricao,
  mental, lifestyle
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from llm import complete_json

from . import store

MODALIDADES = [
    "corrida_road",
    "corrida_trail",
    "utmb_ultra",
    "ciclismo_road",
    "ciclismo_mtb",
    "natacao",
    "hyrox",
    "triatlo",
]

TEMPLATES_VALIDOS = ["stat", "quote", "compare", "carousel_cover", "feature", "mockup", "product"]
# Quizzes ficaram só pro feed; planner pode usar quiz mas writer trata.

BACKBONE = {
    0: ("mindset", "motivação semanal"),
    1: ("ciencia-performance", "protocolo / paper / dado"),
    2: ("produto-merge", "feature / case real"),
    3: ("nutricao_recovery", "nutrição ou recovery"),
    4: ("treino_performance", "treino / performance"),
    5: ("lifestyle", "lifestyle / comunidade"),
    6: ("longevidade_sono", "longevidade / sono"),
}


SYSTEM_PROMPT = """Você é o head de conteúdo da Merge — marca de wellness com foco em esportes de endurance (UTMB, corrida road/trail, ciclismo road/MTB, Hyrox, natação) que está construindo o app/ecossistema único pra atletas amadores e elite.

POSICIONAMENTO MERGE:
- Linguagem direta, sem emoji, sem "vamos falar de", "você sabia", "imagina só"
- Tom de comunidade, não influencer; com propriedade técnica mas sem formalismo
- Stats-first / data-driven (anti bro-science) — sempre cita fonte quando faz claim
- Visual: arte é HOOK CLICKBAIT (puxa pro carrossel) com baixa densidade — 1 stat-bomba ou pergunta provocativa
- Carrossel: aprofundamento progressivo (slide 2 contexto, 3-4 dados, 5 takeaway)
- Legenda: lead direto (1ª linha entrega o fato), frases curtas, CTA seco
- NÃO repetir tópicos exatos em janela de 60 dias

RESTRIÇÕES TÉCNICAS:
- Templates válidos: stat, quote, compare, carousel_cover, feature, mockup, product
- 09:00 = formato leve (stat, quote, feature single)
- 19:00 = formato denso preferencial (carousel_cover, compare, mockup)
- Carrossel só uma vez a cada 3 dias no máx
- 1 slot wildcard reservado pra news reativa (marcar como format="reactive_wildcard")

VOCÊ DEVE:
- Variar modalidades (não 2 consecutivos da mesma)
- Variar pillars (não repetir 2x na mesma semana)
- Considerar eventos próximos do calendário esportivo
- Sugerir séries/storytelling quando faz sentido (3-5 posts conectados)
"""


def _build_user_prompt(
    week_start: datetime,
    n_slots: int,
    recent: list[dict],
    upcoming_events: list[dict],
    insights_hint: str,
) -> str:
    recent_summary = []
    for it in recent[-30:]:  # cap em 30 últimos
        recent_summary.append(
            f"- {it.get('scheduled_at','?')[:10]} · {it.get('template','?')} · "
            f"{it.get('theme','?')} · {it.get('title','')[:80]}"
        )
    events_summary = "\n".join(
        f"- {e['date']} ({e.get('days_ahead','?')}d) · {e['name']} ({e.get('modality','?')})"
        for e in upcoming_events
    ) or "(sem eventos próximos cadastrados)"

    backbone_text = "\n".join(
        f"  {dia}: {pillar} ({hint})"
        for (pillar, hint), dia in zip(
            BACKBONE.values(), ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        )
    )

    return f"""Plano editorial pra semana iniciando {week_start.strftime("%Y-%m-%d")} (segunda).
Total de slots: {n_slots} (7 dias × 2 horários).

BACKBONE TEMÁTICO POR DIA DA SEMANA (use como guia, não como prisão):
{backbone_text}

EVENTOS PRÓXIMOS (use pra puxar temas-âncora 5-7d antes):
{events_summary}

ÚLTIMOS POSTS (NÃO REPETIR temas/headlines/templates idênticos):
{chr(10).join(recent_summary) if recent_summary else "(nenhum)"}

INSIGHTS DE PERFORMANCE (priorize formatos/temas que estão funcionando):
{insights_hint or "(sem dados de performance ainda)"}

DEVOLVA JSON com {n_slots} entries no formato:
[
  {{
    "scheduled_at": "YYYY-MM-DD HH:MM",
    "slot_hour": 9 ou 19,
    "theme": "corrida|bike|natacao|hyrox|forca|nutricao|recovery|prevencao|atletas|news|launch|ciencia|...",
    "modality": "{'|'.join(MODALIDADES)}",
    "format": "static|carousel" (use "static" pra stat/quote/feature/mockup; "carousel" pra carousel_cover/compare),
    "template": "{'|'.join(TEMPLATES_VALIDOS)}",
    "pillar": "ex: educacional-corrida, modalidade-utmb, ciencia-performance, ...",
    "hook_idea": "frase curta de partida (max 80 chars) — o que o slide vai gritar",
    "lead_idea": "1-2 frases descrevendo o gancho técnico/emocional do post",
    "caption_angle": "ângulo da legenda em 1 frase",
    "is_wildcard": false,
    "series_id": null ou "swim_drills_w20" se for parte de série,
    "series_position": null ou 1, 2, 3
  }},
  ...
]

REGRAS HARD:
- 1 slot por semana com is_wildcard=true (reservado pra news reativa)
- Pelo menos 3 modalidades diferentes na semana
- Pelo menos 5 pillars diferentes na semana
- Carrossel no máx 2x na semana
- Slot 09:00 → format leve (stat, quote, feature, mockup); 19:00 → pode ser carousel/compare"""


def _gather_upcoming_events(now: datetime, days_ahead: int = 30) -> list[dict]:
    """
    Lista hard-coded de eventos-âncora endurance. Pode virar config externa
    futuramente; por ora vive aqui pra evitar deps de scraping.
    """
    raw = [
        ("2026-05-17", "Volta a Portugal · stage", "ciclismo"),
        ("2026-05-31", "Maratona de Madrid", "corrida"),
        ("2026-06-07", "Comrades Marathon", "corrida"),
        ("2026-06-14", "Mont-Blanc Marathon (UTMB)", "utmb"),
        ("2026-06-21", "Sundown Marathon SP", "corrida"),
        ("2026-06-29", "Slovenia Ironman", "triatlo"),
        ("2026-07-04", "Tour de France · grand départ", "ciclismo"),
        ("2026-07-12", "Ironman 70.3 Floripa", "triatlo"),
        ("2026-07-19", "Hyrox São Paulo", "hyrox"),
        ("2026-08-29", "UTMB Mont-Blanc · main race", "utmb"),
        ("2026-09-13", "Berlin Marathon", "corrida"),
        ("2026-09-27", "Ironman 70.3 Rio", "triatlo"),
        ("2026-10-12", "Chicago Marathon", "corrida"),
        ("2026-11-02", "NYC Marathon", "corrida"),
        ("2026-11-15", "Maratona de SP", "corrida"),
    ]
    out: list[dict] = []
    horizon = now + timedelta(days=days_ahead)
    for date_str, name, modality in raw:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if d < now or d > horizon:
            continue
        out.append(
            {
                "date": date_str,
                "name": name,
                "modality": modality,
                "days_ahead": (d.date() - now.date()).days,
            }
        )
    return out


def plan_week(
    week_start: datetime,
    *,
    n_slots: int = 14,
    insights_hint: str = "",
) -> list[dict]:
    """Gera plano da semana via Claude. Retorna lista de slots."""
    recent = store.list_recent_briefs(window_days=60)
    events = _gather_upcoming_events(week_start)
    user = _build_user_prompt(week_start, n_slots, recent, events, insights_hint)
    plan = complete_json(
        system=SYSTEM_PROMPT,
        user=user,
        fast=False,  # planner usa Opus pra qualidade
        max_tokens=6000,
        temperature=0.6,
    )
    if not isinstance(plan, list):
        raise ValueError(f"planner devolveu não-lista: {type(plan)}")
    # Sanity: garante scheduled_at ISO bem-formado
    cleaned: list[dict] = []
    for entry in plan[:n_slots]:
        if not isinstance(entry, dict):
            continue
        if "scheduled_at" not in entry:
            continue
        cleaned.append(entry)
    return cleaned
