"""
Geração dos briefs JSON pra cada milestone de uma race.

Countdown (T-30/T-15/T-7/T-1):
  - 1 brief usando template `im_countdown`.
  - Quando race tem `logo` configurado → logo card com a marca da prova.
    Sem logo → pill laranja com sigla (location_short + data).
  - Caption gerada via Claude Sonnet, com prompt específico por modalidade
    (ironman, mtb, trail) — vocabulário e referências do mercado de cada esporte.

Resultados (T+1, só kind=ironman):
  - 3 briefs (capa + masc + fem) usando templates `im_results_cover` + `im_ranking_full`.
  - IDs encadeados: <race>_results, <race>_results.2, <race>_results.3.
    Período (não underscore) é exigência do publish.py:collect_post_images,
    que auto-detecta carrossel via prefixo "<id>.<n>".
  - Caption só na capa; demais slides sem captions próprias.

Briefs são salvos em content/briefs/ pelo runner depois.
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from llm import complete

from .config import parse_race_date

ROOT = Path(__file__).resolve().parent.parent.parent
BG_HISTORY_PATH = ROOT / "output" / ".bg_history.json"
BG_COOLDOWN_DAYS = 60

# ----- countdown -----

CAPTION_SYSTEM = (
    "Você é o copywriter da Merge — comunidade endurance Brasil (triathlon, "
    "mtb, trail running, ciclismo). Comunicação direta, anti bro-science, "
    "stats-first, vocabulário de quem está dentro do esporte. Sem emoji, sem "
    "claim médico, sem 'você sabia', sem 'imagina só'. Português brasileiro, "
    "lowercase no estilo The News/OFitFeed."
)

CAPTION_TONE_BY_KIND = {
    "ironman": (
        "Tom: triathlon BR. Pode mencionar T1/T2, divisão (swim/bike/run), "
        "kona slots, sub-9/sub-10/sub-12 conforme distância, kit IM, age group. "
        "Refira a prova como 'IM <local>' ou pelo nome completo. Distância importa "
        "(full = 226km total / 70.3 = 113km)."
    ),
    "mtb": (
        "Tom: ultramaratona MTB BR. Sertões é 3 etapas (formato stage race), "
        "cross-country marathon (XCM). Pode mencionar altimetria, traçado, "
        "single tracks, rolagem, dupla mista, elite. Sem comparar com road bike. "
        "Vocabulário: 'pelotão', 'etapa rainha', 'GC' (general classification)."
    ),
    "trail": (
        "Tom: trail running. El Cruce é stage race 3 dias na Patagônia (ARG/CHI), "
        "duplas, refúgios. Pode mencionar D+ (desnível positivo), terreno técnico, "
        "altimetria, Saucony (patrocinador), passagem de fronteira. Sem clichê de "
        "'natureza intocada' ou 'desafio'."
    ),
    "marathon": (
        "Tom: corrida de rua, maratona 42km. Pode mencionar pace alvo (sub-3, "
        "sub-3:30, sub-4, sub-5), corte da elite, BQ (Boston Qualifier), "
        "negative split, taper, carb load, parcial 21km. World Athletics label "
        "(gold/elite/heritage) quando fizer sentido. Vocabulário: 'pelotão de "
        "elite', 'baliza', 'cut-off', 'PR' (personal record)."
    ),
    "hyrox": (
        "Tom: hyrox / functional fitness racing. Formato fixo: 8x 1km de corrida "
        "intercalado com 8 estações (skierg, sled push, sled pull, burpee broad "
        "jump, rowing, farmers carry, sandbag lunges, wall balls). Categorias: "
        "Open, Pro, Doubles (mista/masc/fem), Relay. Vocabulário: 'roxer', "
        "'compound time', 'split por estação', 'transição', 'roxzone', "
        "'workout station'. Sem comparar com crossfit ou maratona."
    ),
}

CAPTION_USER_TPL = """Escreva uma legenda CURTA pra Instagram (máx 380 chars) sobre
{name} faltando {days} {days_word} pra largada.

Detalhes:
- Local: {location}
- Data: {date_label}
{distance_line}

{tone_block}

Estrutura obrigatória:
1ª linha — gancho factual (não retórica). pode ser stat, número de dias, ou
contexto de quem vai correr.
2ª linha — stats concretos da prova (distância, formato, ou referência interna).
3ª linha — CTA suave que NÃO seja "vai estar lá?" ou "quem vai?". Varie:
"quem fechou kit", "quem tá no taper", "quem largou inscrição", "tá no plano?",
"quem vai pra cidade?", etc — escolha 1 que faça sentido pro contexto.

Regras duras:
- NÃO usar hashtag. NÃO usar emoji. NÃO usar exclamação.
- NÃO usar "imagina só", "você sabia", "fala galera", "olá pessoal".
- NÃO prometer resultado/treino. NÃO fazer claim médico.
- Lowercase predominante (estilo TNS/OFitFeed). Nomes próprios mantém capitalização."""


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


def _logo_block_html(race: dict) -> str:
    """Constrói o HTML do canto superior direito do template im_countdown.

    - Race com logo configurado E arquivo presente em brand/ → logo card.
      Card branco por padrão; quando `logo_on_dark: true` no yaml (logo é
      branca/clara), card vira preto pra logo aparecer.
    - Caso contrário → pill laranja com sigla curta da prova (location + data).
    """
    logo_file = race.get("logo")
    if logo_file:
        path = (ROOT / "brand" / logo_file).resolve()
        if path.exists():
            cls = "im-cd-logo-card is-dark" if race.get("logo_on_dark") else "im-cd-logo-card"
            return f'<div class="{cls}"><img src="{path.as_uri()}" alt=""></div>'
        # Logo declarada mas arquivo ausente — log e fallback.
        print(f"⚠ logo {logo_file!r} declarada em races.yml mas ausente em brand/")
    label = race.get("race_label_short") or race.get("location_short") or race.get("name", "")
    return f'<span class="im-cd-event-pill">{label}</span>'


# ----- background rotation (60d cooldown) -----

def _load_bg_history() -> dict:
    if not BG_HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(BG_HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_bg_history(hist: dict) -> None:
    BG_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    BG_HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


def _pick_bg(race: dict, today: date | None = None) -> str:
    """Escolhe um BG do pool da race, evitando repetir nos últimos 60 dias.

    Ordem de fallback:
      1. `bg_pool` (lista) — filtra usadas em <60d, sorteia entre as restantes.
         Se todas estão em cooldown, sorteia entre todas (cooldown é soft).
      2. `bg_countdown` (string única, retrocompat) — usa direto.
      3. "" — renderer entende e omite background.
    """
    today = today or datetime.now().date()
    pool: list[str] = race.get("bg_pool") or []
    if not pool:
        single = race.get("bg_countdown", "")
        if single:
            _record_bg_used(single, today)
        return single

    hist = _load_bg_history()
    cutoff = today - timedelta(days=BG_COOLDOWN_DAYS)
    eligible = []
    for img in pool:
        last = hist.get(img)
        if not last:
            eligible.append(img)
            continue
        try:
            last_d = datetime.strptime(last, "%Y-%m-%d").date()
        except ValueError:
            eligible.append(img)
            continue
        if last_d < cutoff:
            eligible.append(img)
    if not eligible:
        # Cooldown soft: ao invés de não publicar, escolhe a mais antiga.
        eligible = sorted(pool, key=lambda i: hist.get(i, "0000-00-00"))[:1]
    chosen = random.choice(eligible)
    _record_bg_used(chosen, today)
    return chosen


def _record_bg_used(img: str, when: date) -> None:
    hist = _load_bg_history()
    hist[img] = when.isoformat()
    _save_bg_history(hist)


def _distance_line(race: dict) -> str:
    kind = race.get("kind", "")
    distance = race.get("distance", "")
    if kind == "ironman" and distance == "full":
        return "- Distância: 3,8 km natação · 180 km bike · 42 km corrida (226 km total)"
    if kind == "ironman" and distance == "70.3":
        return "- Distância: 1,9 km natação · 90 km bike · 21 km corrida (113 km total)"
    if kind == "mtb":
        return "- Formato: stage race / ultramaratona MTB"
    if kind == "trail":
        return "- Formato: stage race trail running"
    if kind == "marathon":
        if distance == "42":
            return "- Distância: 42,195 km · maratona oficial"
        if distance == "21":
            return "- Distância: 21,0975 km · meia-maratona"
        return "- Formato: corrida de rua"
    if kind == "hyrox":
        return "- Formato: 8 km corrida + 8 estações funcionais (skierg, sled push/pull, burpee broad jump, row, farmers carry, sandbag lunges, wall balls)"
    return ""


def _lead_line(race: dict, race_date: date) -> str:
    kind = race.get("kind", "")
    distance = race.get("distance", "")
    loc = race.get("location_short", "").title()
    short = _short_date_pt(race_date)
    if kind == "ironman" and distance == "full":
        return (
            f"3,8 km de natação. 180 km de bike. 42 km de corrida. "
            f"{loc}, {short}. quem fechou kit?"
        )
    if kind == "ironman" and distance == "70.3":
        return (
            f"1,9 km de natação. 90 km de bike. 21 km de corrida. "
            f"{loc}, {short}. quem fechou kit?"
        )
    if kind == "mtb":
        end = race.get("date_end") or race["date"]
        end_d = parse_race_date(end)
        return (
            f"3 etapas de mtb · {loc}, {short}–{_short_date_pt(end_d)}. "
            f"quem tá no pelotão?"
        )
    if kind == "trail":
        end = race.get("date_end") or race["date"]
        end_d = parse_race_date(end)
        return (
            f"stage race em duplas · {loc}, {short}–{_short_date_pt(end_d)}. "
            f"quem fechou inscrição?"
        )
    if kind == "marathon":
        if distance == "42":
            return f"42,195 km · {loc}, {short}. quem tá no taper?"
        if distance == "21":
            return f"21,0975 km · {loc}, {short}. quem tá no taper?"
        return f"corrida de rua · {loc}, {short}. quem tá fechando treino?"
    if kind == "hyrox":
        return (
            f"8 km de corrida + 8 estações · {loc}, {short}. "
            f"quem fechou inscrição?"
        )
    end = race.get("date_end") or race["date"]
    lead = f"{race.get('location','')} · {_date_label_pt(race_date)}"
    if end != race["date"]:
        lead += f" → {_date_label_pt(parse_race_date(end))}"
    return lead + "."


def build_countdown_brief(race: dict, days: int) -> dict:
    """Retorna dict de brief (id, template, vars, caption_md, title)."""
    race_date = parse_race_date(race["date"])
    kind = race.get("kind", "endurance")
    tone_block = CAPTION_TONE_BY_KIND.get(kind, "")

    user = CAPTION_USER_TPL.format(
        name=race["name"],
        days=days,
        days_word=_days_word(days),
        location=race.get("location", ""),
        date_label=_date_label_pt(race_date),
        distance_line=_distance_line(race),
        tone_block=tone_block,
    )
    try:
        caption = complete(system=CAPTION_SYSTEM, user=user, fast=True, max_tokens=400)
    except Exception as e:  # noqa: BLE001
        # Fallback estático — operação não para por causa de API.
        caption = (
            f"faltam {days} {_days_word(days)} pra {race['name']} em "
            f"{race.get('location_short','').lower()}.\n\n"
            f"{race.get('location','')} · {_date_label_pt(race_date)}.\n\n"
            f"quem fechou inscrição?"
        )
        print(f"⚠ caption fallback ({race['id']}): {e!r}")

    # Headline NÃO repete o número de dias (já é protagonista no STAT gigante).
    # Usa tagline motivacional por bucket de proximidade.
    if days == 1:
        headline = 'a prova é <span class="hl">amanhã</span>.'
    elif days <= 7:
        headline = '<span class="hl">semana da prova</span>. tudo já tá feito.'
    elif days <= 15:
        headline = 'afiação. <span class="hl">menos é mais</span> agora.'
    elif days <= 30:
        headline = '<span class="hl">taper</span> começa. recorta o supérfluo.'
    else:
        headline = 'bloco final. <span class="hl">consistência</span> &gt; volume.'

    brief = {
        "id": f"{race['id']}_t{days}",
        "template": "im_countdown",
        "pillar": "ironman" if kind == "ironman" else "endurance",
        "title": f"{race['name']} · T-{days}",
        "vars": {
            "LOGO_BLOCK": _logo_block_html(race),
            "BG_IMAGE": _pick_bg(race),
            "EVENT_LOGO": race.get("logo", ""),
            "KICKER": race.get("kicker", race["name"].upper()),
            "DAYS": str(days),
            "DAYS_UNIT": _days_word(days),
            "HEADLINE": headline,
            "LEAD": _lead_line(race, race_date),
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
