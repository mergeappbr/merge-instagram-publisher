"""
Geração dos briefs JSON pra cada milestone de uma race.

Countdown (T-30/T-15/T-7/T-1):
  - 1 brief usando template `im_countdown` com EVENT_LOGO opcional.
  - Caption gerada via Claude Sonnet (curta, anti claim, anti emoji).

Resultados (T+1, só kind=ironman):
  - 3 briefs (capa + masc + fem) usando templates `im_results_cover` + `im_ranking_full`.
  - IDs encadeados: <race>_results, <race>_results.2, <race>_results.3.
    Período (não underscore) é exigência do publish.py:collect_post_images,
    que auto-detecta carrossel via prefixo "<id>.<n>".
  - Caption só na capa; demais slides sem captions próprias.

Briefs são salvos em content/briefs/ pelo runner depois.
"""
from __future__ import annotations

from datetime import date

from llm import complete

from .config import parse_race_date

# ----- countdown -----

CAPTION_SYSTEM = (
    "Você é o copywriter da Merge — comunicação direta, anti bro-science, "
    "stats-first. Sem emoji, sem claim médico, sem 'você sabia'. Português brasileiro."
)

CAPTION_USER_TPL = """Escreva uma legenda CURTA pra Instagram (máx 380 chars) sobre
{name} faltando {days} {days_word} pra largada.

Detalhes:
- Local: {location}
- Data: {date_label}
{distance_line}

Tom: hype contido, foca em quem vai correr / quem está acompanhando.
Estrutura: 1 linha de gancho + 1 linha de stats da prova + 1 linha CTA suave
("vai estar lá?", "quem vai?", etc).
NÃO usar hashtag. NÃO terminar com pergunta retórica genérica."""


def _days_word(days: int) -> str:
    return "dia" if days == 1 else "dias"


def _date_label_pt(d: date) -> str:
    months = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    return f"{d.day} de {months[d.month - 1]} · {d.year}"


def _short_date_pt(d: date) -> str:
    return d.strftime("%d/%m")


def build_countdown_brief(race: dict, days: int) -> dict:
    """Retorna dict de brief (id, template, vars, caption_md, title)."""
    race_date = parse_race_date(race["date"])
    distance = race.get("distance", "")
    distance_line = ""
    if race.get("kind") == "ironman" and distance == "full":
        distance_line = "- Distância: 3,8 km natação · 180 km bike · 42 km corrida"
    elif race.get("kind") == "ironman" and distance == "70.3":
        distance_line = "- Distância: 1,9 km natação · 90 km bike · 21 km corrida"

    user = CAPTION_USER_TPL.format(
        name=race["name"],
        days=days,
        days_word=_days_word(days),
        location=race.get("location", ""),
        date_label=_date_label_pt(race_date),
        distance_line=distance_line,
    )
    try:
        caption = complete(system=CAPTION_SYSTEM, user=user, fast=True, max_tokens=400)
    except Exception as e:  # noqa: BLE001
        # Fallback estático — operação não para por causa de API.
        caption = (
            f"faltam {days} {_days_word(days)} pra {race['name']} em "
            f"{race.get('location_short','').lower()}.\n\n"
            f"{race.get('location','')} · {_date_label_pt(race_date)}.\n\n"
            f"quem vai estar lá?"
        )
        print(f"⚠ caption fallback ({race['id']}): {e!r}")

    headline = f'faltam <span class="hl">{days} {_days_word(days)}</span> pra largada.'
    if days == 1:
        headline = 'a prova é <span class="hl">amanhã</span>.'

    if race.get("kind") == "ironman" and distance == "full":
        lead = "3,8 km de natação. 180 km de bike. 42 km de corrida. " + (
            f"{race.get('location_short','').title()}, {_short_date_pt(race_date)}. Quem vai estar lá?"
        )
    elif race.get("kind") == "ironman" and distance == "70.3":
        lead = "1,9 km de natação. 90 km de bike. 21 km de corrida. " + (
            f"{race.get('location_short','').title()}, {_short_date_pt(race_date)}. Quem vai estar lá?"
        )
    else:
        end = race.get("date_end") or race["date"]
        lead = f"{race.get('location','')} · {_date_label_pt(race_date)}"
        if end != race["date"]:
            lead += f" → {_date_label_pt(parse_race_date(end))}"
        lead += "."

    brief = {
        "id": f"{race['id']}_t{days}",
        "template": "im_countdown",
        "pillar": "ironman" if race.get("kind") == "ironman" else "endurance",
        "title": f"{race['name']} · T-{days}",
        "vars": {
            "BG_IMAGE": race.get("bg_countdown", ""),
            "EVENT_LOGO": race.get("logo", ""),
            "KICKER": race.get("kicker", race["name"].upper()),
            "DAYS": str(days),
            "DAYS_UNIT": _days_word(days),
            "HEADLINE": headline,
            "LEAD": lead,
            "RACE_LABEL": race.get("race_label_short", ""),
        },
        "caption_md": caption,
    }
    return brief


# ----- results (carrossel, só kind=ironman) -----

RESULTS_CAPTION_SYSTEM = CAPTION_SYSTEM

RESULTS_CAPTION_USER_TPL = """Escreva uma legenda pra um carrossel de resultados
de {name} (top 10 brasileiros M+F). Máx 500 chars.

- Top 1 masc: {top_male}
- Top 1 fem: {top_female}

Tom: factual, celebra sem ser meloso. Estrutura: 1 abertura forte com nomes do
top 1 + 1 linha sobre a profundidade do pelotão BR + 1 fechamento ("arrasta pra
ver os 10 brasileiros" — variação criativa, NÃO usar essa frase específica).
Sem hashtag. Sem emoji.
"""


def _rows_html(athletes: list[dict]) -> str:
    """Constrói as <div class=im-rk-row> a partir da lista parseada."""
    out: list[str] = []
    for a in athletes[:10]:
        pos = int(a.get("pos", 0))
        klass = "im-rk-row"
        if pos == 1:
            klass += " top1"
        elif pos == 2:
            klass += " top2"
        elif pos == 3:
            klass += " top3"
        name = a.get("name", "?").strip()
        time_ = a.get("time", "?").strip()
        out.append(
            f'<div class="{klass}"><div class="im-rk-pos">{pos}</div>'
            f'<div class="im-rk-name">{name}</div>'
            f'<div class="im-rk-time">{time_}</div></div>'
        )
    return "".join(out)


def build_results_briefs(race: dict, results: dict) -> list[dict]:
    """Retorna 3 briefs: capa, top10 masc, top10 fem.

    `results` é o dict de results.py (campos male, female, verified, warnings).
    Apenas a capa carrega caption_md; demais slides têm caption=''.
    """
    race_date = parse_race_date(race["date"])
    base_id = f"{race['id']}_results"
    race_label = (
        f"{race['name'].upper()} · {race.get('location_short','').upper()} "
        f"· {race_date.strftime('%d/%m/%Y')}"
    )

    top_male = (results.get("male") or [{}])[0].get("name", "—")
    top_female = (results.get("female") or [{}])[0].get("name", "—")

    user = RESULTS_CAPTION_USER_TPL.format(
        name=race["name"],
        top_male=top_male,
        top_female=top_female,
    )
    try:
        caption = complete(system=RESULTS_CAPTION_SYSTEM, user=user, fast=True, max_tokens=500)
    except Exception as e:  # noqa: BLE001
        caption = (
            f"resultados {race['name'].lower()}.\n\n"
            f"top 1 masc: {top_male}\n"
            f"top 1 fem: {top_female}\n\n"
            f"top 10 brasileiros nos próximos slides."
        )
        print(f"⚠ caption fallback results ({race['id']}): {e!r}")

    # Headline: "<prefixo> <span class=hl>última palavra</span>."
    # ex: "IRONMAN Brasil" → 'ironman <span class="hl">brasil</span>.'
    parts = race["name"].split()
    if len(parts) >= 2:
        headline = " ".join(parts[:-1]).lower() + f' <span class="hl">{parts[-1].lower()}</span>.'
    else:
        headline = race["name"].lower() + "."

    cover = {
        "id": base_id,
        "template": "im_results_cover",
        "pillar": "ironman",
        "title": f"{race['name']} · capa carrossel resultados",
        "vars": {
            "BG_IMAGE": race.get("bg_results_cover", ""),
            "EVENT_LOGO": race.get("logo", ""),
            "HEADLINE": headline,
            "RACE_LOCATION": race.get("location", ""),
            "RACE_DATE": _date_label_pt(race_date),
            "SLIDE_TOTAL": "3",
        },
        "caption_md": caption,
    }

    male_brief = {
        "id": f"{base_id}.2",
        "template": "im_ranking_full",
        "pillar": "ironman",
        "title": f"{race['name']} · top 10 masc",
        "vars": {
            "BG_IMAGE": race.get("bg_results_male", ""),
            "EVENT_LOGO": race.get("logo", ""),
            "GENDER_LABEL": "masculino",
            "RACE_LABEL": race_label,
            "SLIDE_NUM": "2",
            "SLIDE_TOTAL": "3",
            "ROWS_HTML": _rows_html(results.get("male") or []),
        },
        "caption_md": "",
    }

    female_brief = {
        "id": f"{base_id}.3",
        "template": "im_ranking_full",
        "pillar": "ironman",
        "title": f"{race['name']} · top 10 fem",
        "vars": {
            "BG_IMAGE": race.get("bg_results_female", ""),
            "EVENT_LOGO": race.get("logo", ""),
            "GENDER_LABEL": "feminino",
            "RACE_LABEL": race_label,
            "SLIDE_NUM": "3",
            "SLIDE_TOTAL": "3",
            "ROWS_HTML": _rows_html(results.get("female") or []),
        },
        "caption_md": "",
    }

    return [cover, male_brief, female_brief]
