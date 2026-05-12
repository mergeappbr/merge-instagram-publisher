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
  Em NEWS (template=news_magazine): LEAD é UMA frase curta, MÁX 90 chars
  (~12 palavras), SEM <br>, SEM listas, SEM reticências, terminando em ponto.
  Função: contextualizar o headline em 1 linha que CABE no card centralizado.
  A fundo vai pro caption_md. Exemplo BOM: "Geometria revisada e peso menor
  miram triathletas mais leves." (78 chars). Exemplo RUIM: "A Cervélo
  atualizou a Aspero e a quinta geração chega com foco direto em triathletas
  que querem uma bike versátil sem abrir mão de aero." (>140 chars, vai
  estourar a caixa).

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
            "- HEADLINE — DESTAQUE LARANJA *OBRIGATÓRIO* (REGRA CRÍTICA):\n"
            "  O template tem watermark 'merge.' no topo. A HEADLINE é o\n"
            "  protagonista visual e PRECISA ter EXATAMENTE 1 trecho envolvido\n"
            "  em <span class=\"hl\">...</span> que vai pra laranja na arte.\n"
            "  SEM ESSE SPAN = HEADLINE INVÁLIDA → VOID. NÃO entregue brief\n"
            "  sem o span. Quem destacar (ordem de prioridade):\n"
            "    1. Nome de pessoa (atleta, treinador, cientista) → ex:\n"
            "       '<span class=\"hl\">Kipchoge</span> anuncia última maratona'\n"
            "    2. Nome de produto/dispositivo → ex:\n"
            "       'Google lança <span class=\"hl\">Fitbit Air</span>, wearable...'\n"
            "    3. Nome de marca/organização → ex:\n"
            "       '<span class=\"hl\">WHOOP</span> 5.0 mede pressão arterial'\n"
            "    4. Nome de evento/prova/local → ex:\n"
            "       'Taper na <span class=\"hl\">Maratona POA</span>: o que cortar'\n"
            "    5. Conceito-chave (só se NÃO houver nome próprio) → ex:\n"
            "       '<span class=\"hl\">cafeína</span> melhora desempenho em 12%'\n"
            "  EXEMPLO CONCRETO desta notícia: se título é 'Maratona Olympikus\n"
            "  de Porto Alegre 2026...', a HEADLINE deve ter\n"
            "  <span class=\"hl\">Maratona POA</span> ou <span class=\"hl\">Olympikus</span>.\n"
            "  RUIM (sem destaque, todo branco): 'Google lança Fitbit Air,\n"
            "  wearable no formato pulseira' — VOID. Refaça com span.\n"
            "- ESPAÇOS (REGRA CRÍTICA): NUNCA junte palavras sem espaço entre\n"
            "  elas. Cada palavra separada por exatamente 1 espaço. Erros como\n"
            "  'provaprecisa', 'PortoAlegre.Faltando', 'taperpra' são VOID e\n"
            "  invalidam o brief. Releia HEADLINE e LEAD palavra por palavra\n"
            "  ANTES de devolver pra garantir que TODOS os espaços estão lá,\n"
            "  inclusive depois de pontuação ('. ', ', ', ': ', '— ').\n"
            "- IDIOMA: TRADUZA TUDO pra português brasileiro. Mesmo que título/\n"
            "  summary venham em inglês (Outside, Runner's World, Velo, etc),\n"
            "  HEADLINE, LEAD e caption_md são SEMPRE em pt-BR. Mantém em inglês\n"
            "  só nomes próprios (atletas, marcas, provas: UTMB, Hyrox, WHOOP,\n"
            "  Fitbit) e termos técnicos sem tradução boa ('triathlon', 'pace',\n"
            "  'VO2max', 'split'). Voz Merge: direta, técnica, sem clichê de\n"
            "  tradução automática ('De acordo com...', 'Foi reportado que...').\n"
            "- Vocabulário pt-BR: use 'pace' (NUNCA 'pacing' — esse termo não\n"
            "  existe em português). Use 'ritmo' como alternativa quando fizer sentido.\n"
            "- Caption MAIS densa que post comum: 4-6 parágrafos curtos, com dados\n"
            "  concretos da notícia (números, atletas, organizações, datas).\n"
            "- INCLUA 1 bloco de bullet points com 3-5 pontos-chave da notícia.\n"
            "  LABEL DO BLOCO — VARIE conforme o tipo de conteúdo (NUNCA use\n"
            "  'Destaques:' como default fixo). Escolha 1 que case com o tom:\n"
            "    * Lançamento de produto/anúncio → 'O que importa:' /\n"
            "      'Os números:' / 'Especificações:'\n"
            "    * Resultado de evento/prova → 'O que rolou:' /\n"
            "      'Resultados:' / 'Pódio:'\n"
            "    * Estudo/pesquisa científica → 'Os achados:' / 'O estudo\n"
            "      em 4 pontos:' / 'O que a ciência diz:'\n"
            "    * Treino/método/técnica → 'Como aplicar:' / 'O passo a passo:'\n"
            "      / 'O protocolo:'\n"
            "    * Pré-prova/prep → 'Pra fazer agora:' / 'Checklist:' /\n"
            "      'Plano de ação:'\n"
            "    * Tendência/análise → 'O cenário:' / 'Por que isso\n"
            "      importa:' / 'Pontos críticos:'\n"
            "  Pode improvisar OUTROS labels que façam sentido editorial. EVITE\n"
            "  'Destaques:' a menos que seja LITERALMENTE um resumo de\n"
            "  highlights (ex: melhores momentos de evento). Formato dentro\n"
            "  do caption_md (linha em branco antes/depois do label):\n"
            "\n"
            "      <label_escolhido>:\n"
            "\n"
            "      - <ponto 1, com número/dado concreto>\n"
            "      - <ponto 2>\n"
            "      - <ponto 3>\n"
            "\n"
            "- BG_IMAGE: OBRIGATÓRIO em news. NUNCA devolva vazio. ORDEM DE\n"
            "  PRIORIDADE (use FILES diretos quando existir, slug bank é fallback):\n"
            "    * Corrida/maratona em geral: PREFIRA 'marathon.jpg' (file direto,\n"
            "      foto curada de silhuetas correndo no nascer do sol — visual\n"
            "      icônico). Slug 'marathon_finish_line' só se quer especificamente\n"
            "      momento de chegada com público.\n"
            "    * Tech/wearables/dados/apps: 'smartwatch_running_watch'.\n"
            "    * Natação: 'swimmer_pool_training'.\n"
            "    * Lesão/fisio/recovery: 'running_injury_physiotherapy'.\n"
            "    * Trail running: 'trail_running_effort'.\n"
            "    * Pista/sprint/elite: 'track_sprint_athlete'.\n"
            "  Se a notícia for de tema sem imagem exata no banco (ex: ciclismo,\n"
            "  triathlon, hyrox), escolha a MAIS PRÓXIMA: 'track_sprint_athlete'\n"
            "  pra esforço atlético genérico, OU 'smartwatch_running_watch' pra\n"
            "  notícia técnica/dados. Jamais deixe BG_IMAGE em branco em news.\n"
            "- FONTES NOMINAIS: cite a fonte (publicação, organização, atleta) pelo\n"
            "  NOME no caption_md — ex: 'segundo a Velo (Outside)', 'World Athletics\n"
            "  anunciou', 'estudo da Universidade de Salzburg'. NUNCA escreva 'a\n"
            "  fonte' ou 'o estudo' sem nomear.\n"
            "- TEMPLATE: 'feature' por default (1 imagem direta). Use 'carousel_cover'\n"
            "  quando a notícia tem 3+ achados/estatísticas distintos — fica melhor\n"
            "  abrir cada ponto num slide próprio. Se carousel_cover, devolva\n"
            "  carousel_slides com 3-4 slides (cada um {PILL,HEADLINE,LEAD}).\n"
            "- Tom: direto, OFitFeed / The News (TNS) — sem floreio.\n"
            "- STORY LEAD (em story_vars.LEAD): NUNCA use 'link nos destaques',\n"
            "  'link na bio', 'swipe up', 'arrasta pra cima', 'matéria completa\n"
            "  no destaque'. Merge NÃO oferece esses serviços. O fechamento de\n"
            "  story de news SEMPRE aponta pro próprio post no feed. Use:\n"
            "  'matéria completa no feed', 'post no feed pra ver tudo',\n"
            "  'detalhes no post fixo'. Pode também ser uma pergunta direta\n"
            "  sem CTA ('quanto isso muda seu treino?').\n"
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
        parts.append(
            f"\n=== INSTRUÇÃO DO HUMANO (PRIORIDADE MÁXIMA) ===\n{adjust_instruction}\n"
            f"=== FIM DA INSTRUÇÃO ===\n"
        )
        parts.append(
            "REGRAS DO REGEN:\n"
            "1. A INSTRUÇÃO DO HUMANO ACIMA É A PRIORIDADE ABSOLUTA. Sobrepõe\n"
            "   QUALQUER regra default (incluindo regras de BG_IMAGE, vocabulário,\n"
            "   tom). Se o humano pediu pra trocar BG_IMAGE pra X, troca pra X\n"
            "   mesmo que X não esteja no banco — escreve o caminho exato que\n"
            "   ele pediu.\n"
            "2. Aplique com CIRURGIA: só mude o que foi pedido. Mantém HEADLINE,\n"
            "   LEAD, caption_md inalterados se a instrução foi sobre BG. Mantém\n"
            "   BG inalterado se a instrução foi sobre texto.\n"
            "3. Se a instrução cita pesquisa/contexto novo (ex: 'pesquise mais\n"
            "   sobre X'), incorpore esse ângulo no LEAD/caption mesmo que\n"
            "   contradiga o angle anterior. Confiança no que o humano sabe."
        )

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
        '    "LEAD": "<lead pra story; CTA pode ser DM/enquete OU pergunta seca; em news, aponta pro post no feed (NUNCA destaques/bio/swipe)>"\n'
        '  },\n'
        '  "caption_md": "<caption em markdown plano: 1ª linha = hook, depois corpo separado por linha em branco, CTA final>",\n'
        '  "carousel_slides": [<APENAS se template=carousel_cover, lista de 3-4 slides com {PILL,HEADLINE,LEAD}>]\n'
        '}\n\n'
        'Inclua APENAS as chaves usadas pelo template. NÃO inclua chave que o template não consome.'
    )
    return "\n".join(parts)


_HL_RE = re.compile(r'<span\s+class=["\']hl["\']\s*>', re.IGNORECASE)
# Palavras coladas: pontuação seguida de palavra "normal" (Maiúscula + 2+ lower).
# Evita quebrar siglas tipo 'U.S.A.' ou 'B.O.' onde o próximo char é só 1 letra.
_PUNCT_NOSPACE_RE = re.compile(r'([\.\,\:\;\!\?\—\–])([A-ZÀ-Ý][a-zà-ÿ]{2,})')
# camelCase grudado: lower + UPPER (ex: 'PortoAlegre' → 'Porto Alegre')
_CAMEL_GLUE_RE = re.compile(r'([a-zà-ÿ])([A-ZÀ-Ý])')

_STOP_CAPS = {
    "O", "A", "Os", "As", "Um", "Uma", "De", "Do", "Da", "Dos", "Das",
    "No", "Na", "Nos", "Nas", "Em", "Para", "Pra", "Com", "Sem", "Por",
    "E", "Ou", "Se", "Já", "Após", "Antes", "Sobre", "Como", "Quanto",
    "O que", "A que",
}


def _fix_concatenations(text: str) -> str:
    """Conserta palavras coladas óbvias (sem heurística agressiva)."""
    if not text:
        return text
    text = _PUNCT_NOSPACE_RE.sub(r'\1 \2', text)
    text = _CAMEL_GLUE_RE.sub(r'\1 \2', text)
    return text


def _autoinject_hl(headline: str, news_context: dict) -> str:
    """Se HEADLINE de news não tem <span class='hl'>, envolve nome próprio óbvio.

    Heurística em camadas:
      1. Match 1-3 tokens capitalizados que aparecem na HEADLINE E no título.
      2. Match 1 token isolado (nome próprio standalone) na HEADLINE.
      3. Sigla tudo-maiúsculo (POA, UTMB, WHOOP, USA) com 3+ letras.
    Devolve sem mudança se nada plausível encontrado.
    """
    if not headline or _HL_RE.search(headline):
        return headline
    plain_hl = re.sub(r"<[^>]+>", "", headline)
    title = (news_context.get("title") or "")

    # Camada 1: candidatos 1-3 tokens E candidatos 1 token isolado do TÍTULO
    title_multi = re.findall(
        r"\b[A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+){1,2}\b", title
    )
    title_single = re.findall(r"\b[A-ZÀ-Ý][\wÀ-ÿ]+\b", title)
    title_caps = list(dict.fromkeys(title_multi + title_single))  # dedupe, preserva ordem
    title_caps = [t for t in title_caps if t.split()[0] not in _STOP_CAPS]
    title_caps.sort(key=len, reverse=True)  # prefere match mais longo
    for cand in title_caps:
        if cand in plain_hl:
            return headline.replace(cand, f'<span class="hl">{cand}</span>', 1)

    # Camada 2: 1º token Capitalizado da HEADLINE que não seja stopword
    tokens = re.findall(r"\b[\wÀ-ÿ]+\b", plain_hl)
    for tok in tokens:
        if tok in _STOP_CAPS:
            continue
        if re.match(r"^[A-ZÀ-Ý][\wÀ-ÿ]+$", tok):
            return headline.replace(tok, f'<span class="hl">{tok}</span>', 1)

    # Camada 3: sigla tudo-maiúsculo (POA, UTMB, WHOOP) com 3+ letras
    acronyms = re.findall(r"\b[A-Z]{3,}\b", plain_hl)
    if acronyms:
        return headline.replace(acronyms[0], f'<span class="hl">{acronyms[0]}</span>', 1)

    return headline


def _shrink_lead_to_one_sentence(lead: str, max_chars: int = 95) -> str:
    """News_magazine pede LEAD de 1 frase curta que CABE no card centralizado.
    NUNCA usa reticências — Pedro odeia. Estratégia em camadas:
      1. Remove <br>, colapsa espaços.
      2. Se há ponto final no meio, corta na 1ª frase completa.
      3. Se a frase resultante ainda > max_chars, busca uma vírgula/quebra
         natural ANTES do limite e corta ali, fechando com ponto.
      4. Último recurso: corta na última palavra inteira antes do limite e
         fecha com ponto.
    Resultado sempre termina com ponto/!/? e nunca com "…".
    """
    if not lead:
        return lead
    s = re.sub(r"<br\s*/?>", " ", lead, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    # Camada 1: primeira frase completa
    m = re.search(r"(.+?[.!?])(\s|$)", s)
    if m and len(m.group(1)) <= max_chars:
        return m.group(1).strip()
    # Camada 2: corta numa vírgula/ponto-vírgula/travessão antes do limite
    head = s[:max_chars]
    for sep in [", ", "; ", " — ", " – "]:
        idx = head.rfind(sep)
        if idx >= max_chars * 0.5:  # vírgula precisa estar na 2ª metade
            cut = head[:idx].rstrip(",;:—- ")
            return cut + "."
    # Camada 3: corta na última palavra inteira
    cut = head.rsplit(" ", 1)[0].rstrip(",;:—- ")
    if not cut.endswith((".", "!", "?")):
        cut += "."
    return cut


def _postprocess_news_brief(brief: dict, news_context: dict | None) -> None:
    """Aplica auto-fixes em briefs de news. Modifica in-place."""
    if not news_context:
        return
    vars_ = brief.get("vars", {})
    if "HEADLINE" in vars_:
        vars_["HEADLINE"] = _fix_concatenations(_autoinject_hl(vars_["HEADLINE"], news_context))
    if "LEAD" in vars_:
        vars_["LEAD"] = _fix_concatenations(vars_["LEAD"])
    sv = brief.get("story_vars") or {}
    if "HEADLINE" in sv:
        sv["HEADLINE"] = _fix_concatenations(_autoinject_hl(sv["HEADLINE"], news_context))
    if "LEAD" in sv:
        sv["LEAD"] = _fix_concatenations(sv["LEAD"])
    if "caption_md" in brief and isinstance(brief["caption_md"], str):
        brief["caption_md"] = _fix_concatenations(brief["caption_md"])
    # news_magazine: LEAD precisa ser 1 frase curta (diagramação centralizada).
    # Aplica só no template news_magazine — feed_post seta isso ANTES do reviewer,
    # mas no fluxo writer→postprocess o template ainda não foi forçado, então
    # detectamos por news_context presente (já é sinal de news).
    if "LEAD" in vars_:
        vars_["LEAD"] = _shrink_lead_to_one_sentence(vars_["LEAD"])


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
    _postprocess_news_brief(brief, news_context)
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
    _postprocess_news_brief(brief, news_context)
    return brief
