"""
Writer — recebe entry do planner (ou contexto reativo de news) e devolve:
  - brief JSON (formato content/briefs/*.json)
  - caption markdown (formato content/captions.md)

Não escreve em disco; é puro: o runner orquestra persistência + render.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from llm import complete_json

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES_DIR = ROOT / "templates"
IMAGE_BANK_DIR = ROOT / "brand" / "images" / "_bank"


SYSTEM_PROMPT = """Você é o redator-chefe da Merge — wellness focado em endurance (UTMB, corrida road/trail, ciclismo road/MTB, Hyrox, natação).

DIRETRIZES OBRIGATÓRIAS DE LINGUAGEM:
- Sem emoji
- Sem "vamos falar sobre", "você sabia", "imagina só", "olha que interessante"
- Lead direto: 1ª linha entrega o fato/dado
- Frases curtas; voz ativa
- Sem promessas médicas ("cura", "garante", "100% efetivo")
- Cita fonte quando faz claim científico (paper, atleta, organização)
- Tom de comunidade: "a gente", "você", "manda na caixinha" — nunca "querido leitor"

VOCABULÁRIO OBRIGATÓRIO (regras de marca):
- "triathlon" SEMPRE em inglês. NUNCA "triatlo", "triatlon" ou "triathlo".
- "pace" (NUNCA "pacing"). Pode usar "ritmo" como sinônimo em pt-BR.
- Modalidades em pt-BR: "corrida", "trail", "MTB", "natação", "Hyrox", "ciclismo".
  Mas "triathlon" e nomes de provas (UTMB, Cocodona, Hyrox SP) ficam em inglês.

FORMATO DA ARTE (HEADLINE + LEAD):
- HEADLINE: clickbait honesto, max 7 palavras, 1 grupo destacado entre <span class="hl"> ... </span>
  Exemplos bons: "RPE: a escala que <span class="hl">substitui o relógio</span>"
                 "o longão é onde <span class="hl">a cabeça</span> ganha massa"
- LEAD: até 3 linhas, separadas por <br> quando faz lista; usa <br><br> entre parágrafos
  Em arte de stat: deixa o número GIGANTE; o lead complementa em 1 frase

FORMATO DA LEGENDA (caption_md) — REGRAS RÍGIDAS:
- Hook do post (1ª frase): repete/expande a headline
- Corpo: 2-4 parágrafos curtos, dados concretos, exemplos
- CTA final: pergunta ou comando seco (ex: "Salva esse post.", "Manda o seu na caixinha.")
- **NUNCA** inclua linhas tipo `**Story tip:**`, `**Hook do post:**`, `**Caption:**`
  ou qualquer metadata `**Xxx:**` dentro do `caption_md`. Esses marcadores são
  diretivas internas — vão em outros campos, NÃO no caption_md.
- Quando listar itens (faixas, zonas, etapas, comparações), separe CADA item
  numa linha própria com **linha em branco entre eles**, NÃO em linha única
  separada por ponto-mediano. Exemplo BOM:
      "RPE é a escala 1-10:

      1-2 caminhada ·

      3-4 leve ·

      5-6 moderado (ainda fala) ·

      7-8 forte (frase curta) ·

      9-10 máximo (não fala)."
  Exemplo RUIM (NUNCA faça): "1-2 caminhada · 3-4 leve · 5-6 moderado · ..."
- Quando mudar de assunto/seção dentro da legenda, use linha em branco para
  separar visualmente. Não amontoe parágrafos.

CARROSSEL (quando template=carousel_cover):
- Slide 1 = capa (HEADLINE + LEAD do brief master)
- Slides 2-5 = aprofundamento progressivo. Você devolve "carousel_slides" com 3-4 slides extras.
"""


def _slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text[:max_len] or "post"


def _list_image_themes() -> list[str]:
    """Lista pastas do banco de imagens pro LLM escolher."""
    if not IMAGE_BANK_DIR.exists():
        return []
    return sorted(p.name for p in IMAGE_BANK_DIR.iterdir() if p.is_dir())


def _list_template_files() -> list[str]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(
        p.stem for p in TEMPLATES_DIR.glob("*.html") if p.stem != "base"
    )


def _build_user_prompt(
    plan_entry: dict,
    *,
    redundancy_warnings: list[str] | None = None,
    news_context: dict | None = None,
    adjust_instruction: str | None = None,
    previous_brief: dict | None = None,
) -> str:
    image_themes = _list_image_themes()
    templates = _list_template_files()

    parts = []
    parts.append("CONTEXTO DO SLOT")
    parts.append(f"- scheduled_at: {plan_entry.get('scheduled_at','?')}")
    parts.append(f"- slot_hour: {plan_entry.get('slot_hour','?')}")
    parts.append(f"- theme: {plan_entry.get('theme','?')}")
    parts.append(f"- modality: {plan_entry.get('modality','?')}")
    parts.append(f"- format: {plan_entry.get('format','static')}")
    parts.append(f"- template: {plan_entry.get('template','feature')}")
    parts.append(f"- pillar: {plan_entry.get('pillar','?')}")
    parts.append(f"- hook_idea: {plan_entry.get('hook_idea','?')}")
    parts.append(f"- lead_idea: {plan_entry.get('lead_idea','?')}")
    parts.append(f"- caption_angle: {plan_entry.get('caption_angle','?')}")
    if plan_entry.get("series_id"):
        parts.append(
            f"- series: {plan_entry['series_id']} (pos {plan_entry.get('series_position','?')})"
        )

    if news_context:
        parts.append("\nCONTEXTO DA NOTÍCIA (post reativo):")
        parts.append(f"- título: {news_context.get('title','?')}")
        parts.append(f"- fonte: {news_context.get('source','?')}")
        parts.append(f"- url: {news_context.get('url','?')}")
        parts.append(f"- summary: {news_context.get('summary','?')[:600]}")
        parts.append(f"- evento_finalizado: {news_context.get('post_event','?')}")
        parts.append(
            "\nREGRAS OBRIGATÓRIAS PARA NEWS:\n"
            "- PILL: SEMPRE 'NEWS' (curto, 4 letras). NUNCA 'PESQUISA', 'ESTUDO',\n"
            "  'PAPER', 'ANÁLISE', 'STUDY', 'NEWS RESEARCH'. É só 'NEWS'.\n"
            "- Vocabulário pt-BR: use 'pace' (NUNCA 'pacing' — esse termo não\n"
            "  existe em português). Use 'ritmo' como alternativa quando fizer sentido.\n"
            "- Caption MAIS densa que post comum: 4-6 parágrafos curtos, com dados\n"
            "  concretos da notícia (números, atletas, organizações, datas).\n"
            "- INCLUA 1 bloco de bullet points em destaque com os 3-5 pontos-chave\n"
            "  da notícia. Formato dentro do caption_md (linha em branco antes/depois):\n"
            "\n"
            "      Destaques:\n"
            "\n"
            "      - <ponto 1, com número/dado concreto>\n"
            "      - <ponto 2>\n"
            "      - <ponto 3>\n"
            "\n"
            "- BG_IMAGE: para corrida feminina/maratona use 'marathon.jpg' OU o\n"
            "  slug do banco 'marathon_finish_line' (resolve auto). Para corrida\n"
            "  geral use 'track_sprint_athlete' ou 'trail_running_effort'. Para\n"
            "  fisioterapia/lesão use 'running_injury_physiotherapy'. Sempre prefira\n"
            "  imagem que combina com o ângulo da notícia.\n"
            "- TEMPLATE: 'feature' por default (1 imagem direta). Use 'carousel_cover'\n"
            "  quando a notícia tem 3+ achados/estatísticas distintos — fica melhor\n"
            "  abrir cada ponto num slide próprio. Se carousel_cover, devolva\n"
            "  carousel_slides com 3-4 slides (cada um {PILL,HEADLINE,LEAD}).\n"
            "- Tom: direto, OFitFeed / The News (TNS) — sem floreio.\n"
        )

    parts.append(f"\nTEMPLATES DISPONÍVEIS: {', '.join(templates)}")
    if image_themes:
        parts.append(
            f"BANCO DE IMAGENS (use o slug exato como BG_IMAGE quando o brief precisar): {', '.join(image_themes[:60])}..."
        )

    if redundancy_warnings:
        parts.append("\nATENÇÃO — temas/títulos potencialmente redundantes nos últimos 60d:")
        for w in redundancy_warnings[:10]:
            parts.append(f"- {w}")
        parts.append("Garanta ângulo diferente.")

    if previous_brief and adjust_instruction:
        parts.append("\nVERSÃO ATUAL DO BRIEF (você está REGENERANDO com instrução do humano):")
        import json as _json
        parts.append(_json.dumps(previous_brief, ensure_ascii=False, indent=2))
        parts.append(f"\nINSTRUÇÃO DO HUMANO: {adjust_instruction}")
        parts.append("Aplique a instrução com cirurgia. Não reescreva o que não foi pedido.")

    parts.append(
        '\nDEVOLVA JSON único com este formato:\n'
        '{\n'
        '  "id": "<slug curto, max 40 chars, snake_case, sem prefixo numérico>",\n'
        '  "template": "<um dos templates disponíveis>",\n'
        '  "pillar": "<pillar>",\n'
        '  "title": "<título interno do brief, descritivo>",\n'
        '  "vars": {\n'
        '    "PILL": "<rótulo curto MAIÚSCULO, ex: CORRIDA, RPE, BIKE FIT>",\n'
        '    "HEADLINE": "<headline com <span class=\\"hl\\">grupo destacado</span>>",\n'
        '    "LEAD": "<até 3 linhas, separa com <br> ou <br><br>>",\n'
        '    "BG_IMAGE": "<slug da pasta do banco OU caminho específico, ex: villarinho_track.jpg; OPCIONAL>",\n'
        '    "OVERLAY": "side|bottom (opcional)",\n'
        '    "BG_POSITION": "ex: center 40% (opcional)",\n'
        '    "STAT_NUM": "<para template stat: número grande, ex: 73M, 4:35, 30%>",\n'
        '    "STAT_KICKER": "<rótulo curto acima do número, ex: DROP, KM>"\n'
        '  },\n'
        '  "story_vars": {\n'
        '    "PILL": "<reaproveita ou simplifica>",\n'
        '    "HEADLINE": "<versão pra story, geralmente mais curta/perguntativa>",\n'
        '    "LEAD": "<lead pra story; pode ter call to action de DM/enquete>"\n'
        '  },\n'
        '  "caption_md": "<caption em markdown plano: 1ª linha = hook, depois corpo separado por linha em branco, CTA final>",\n'
        '  "carousel_slides": [<APENAS se template=carousel_cover, lista de 3-4 slides com {PILL,HEADLINE,LEAD}>]\n'
        '}\n\n'
        'Inclua APENAS as chaves usadas pelo template. NÃO inclua chave que o template não consome.'
    )
    return "\n".join(parts)


def write_brief(
    plan_entry: dict,
    *,
    redundancy_warnings: list[str] | None = None,
    news_context: dict | None = None,
) -> dict:
    """Gera brief novo (do zero)."""
    user = _build_user_prompt(
        plan_entry,
        redundancy_warnings=redundancy_warnings,
        news_context=news_context,
    )
    brief = complete_json(
        system=SYSTEM_PROMPT,
        user=user,
        fast=True,  # writer usa Sonnet (volume alto, temas constritos)
        max_tokens=4000,
        temperature=0.65,
    )
    if not isinstance(brief, dict):
        raise ValueError(f"writer devolveu não-dict: {type(brief)}")
    if "id" not in brief or "vars" not in brief:
        raise ValueError(f"brief incompleto: {list(brief.keys())}")
    # Garante story_vars não-vazio
    if not brief.get("story_vars"):
        brief["story_vars"] = {
            "PILL": brief["vars"].get("PILL", ""),
            "HEADLINE": brief["vars"].get("HEADLINE", ""),
            "LEAD": brief["vars"].get("LEAD", ""),
        }
    return brief


def regenerate_brief(
    plan_entry: dict,
    previous_brief: dict,
    instruction: str,
    *,
    news_context: dict | None = None,
) -> dict:
    """Regenera brief existente com instrução de ajuste do humano."""
    user = _build_user_prompt(
        plan_entry,
        previous_brief=previous_brief,
        adjust_instruction=instruction,
        news_context=news_context,
    )
    brief = complete_json(
        system=SYSTEM_PROMPT,
        user=user,
        fast=True,
        max_tokens=4000,
        temperature=0.5,
    )
    if not isinstance(brief, dict):
        raise ValueError(f"writer devolveu não-dict: {type(brief)}")
    return brief
