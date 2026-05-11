"""Gemini-driven carousel spec generator.

Recebe (entity, photos, news_title, news_summary) → devolve um
`ProductCarousel` pronto pra render.

Decide número de slides baseado em quantas fotos válidas chegam:
  - 1 foto  → 3 slides: cover, features, cta
  - 2 fotos → 4 slides: cover, hero_photo, features, cta
  - 3 fotos → 5 slides: cover, hero_photo, story, features, cta
  - 4+ fotos → 6 slides: cover, hero_photo, story, features, comparison, cta

Photo role assignment é feito ANTES de chamar Gemini, baseado em
shot_type/angle (quando vision metadata existe). Gemini só preenche o
copy textual de cada slide via JSON schema response.

Fallback: se Gemini falhar, devolve spec mínima (3 slides) com texto
genérico extraído do title/summary.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from product_post import (  # noqa: E402
    ComparisonRow,
    ComparisonSlide,
    CoverSlide,
    CTASlide,
    Feature,
    FeaturesSlide,
    HeroPhotoSlide,
    ProductCarousel,
    StorySlide,
)

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)

CACHE_DIR = ROOT / "output" / "specs"


# ----- photo role assignment -----

def _photo_role(vision: Optional[dict]) -> str:
    """Mapeia vision metadata → role usado no template.

    Roles: hero (cover/cta), detail (hero_photo/features), context (story),
    top (features alt).
    """
    if not vision:
        return "hero"
    shot = (vision.get("shot_type") or "").lower()
    angle = (vision.get("angle") or "").lower()
    if shot == "lifestyle":
        return "context"
    if shot == "detail":
        return "detail"
    if "top" in angle:
        return "top"
    return "hero"


def _assign_photos(photos: list[dict]) -> dict[str, Path]:
    """Distribui fotos para roles. Garante que cada role tem pelo menos
    uma foto se houver foto disponível (fallback pra primeira).
    """
    by_role: dict[str, Path] = {}
    leftovers: list[Path] = []
    for p in photos:
        role = _photo_role(p.get("vision"))
        if role not in by_role:
            by_role[role] = p["path"]
        else:
            leftovers.append(p["path"])

    # Fill missing roles com leftovers ou primeira foto disponível
    fallback = photos[0]["path"] if photos else None
    for role in ("hero", "detail", "context", "top"):
        if role not in by_role:
            if leftovers:
                by_role[role] = leftovers.pop(0)
            elif fallback:
                by_role[role] = fallback
    return by_role


# ----- gemini call -----

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s or "produto"


def _gemini_generate_copy(
    entity: dict,
    photos_meta: list[dict],
    news_title: str,
    news_summary: str,
    n_slides: int,
) -> Optional[dict]:
    """Chama Gemini Flash com prompt detalhado + response_schema JSON.

    Retorna dict com chaves por slide type. None se falhar.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None

    name = entity.get("name", "")
    brand = entity.get("brand") or ""
    category = entity.get("category") or "product"

    # Resumo das fotos pra dar contexto visual ao modelo
    photo_lines = []
    for i, p in enumerate(photos_meta, 1):
        v = p.get("vision") or {}
        photo_lines.append(
            f"  foto {i}: shot={v.get('shot_type','?')}, "
            f"angle={v.get('angle','?')}, notes={v.get('notes','')[:80]}"
        )
    photos_block = "\n".join(photo_lines) if photo_lines else "  (sem metadata)"

    # Slide types ativos
    slide_types = ["cover"]
    if n_slides >= 4:
        slide_types.append("hero_photo")
    if n_slides >= 5:
        slide_types.append("story")
    slide_types.append("features")
    if n_slides >= 6:
        slide_types.append("comparison")
    slide_types.append("cta")

    schema_hint = (
        '{\n'
        '  "slug": "<snake_case do produto>",\n'
        '  "cover": {\n'
        '    "kicker": "<TIPO · MARCA NOME> (UPPERCASE, ex: RACING SHOE · NIKE VAPORFLY 4)",\n'
        '    "stat_num": "<número-chave>",\n'
        '    "stat_unit": "<g|km|mm|R$|null>",\n'
        '    "stat_caption": "<o que é a stat, UPPERCASE>",\n'
        '    "headline_html": "<lowercase, ≤8 palavras, com <span class=\\"hl\\">termo</span>>",\n'
        '    "lead": "<EXATAMENTE 1 frase ≤18 palavras, clickbait c/ 1 dado técnico real do resumo>"\n'
        '  },\n'
    )
    if "hero_photo" in slide_types:
        schema_hint += (
            '  "hero_photo": {\n'
            '    "kicker": "<DESCRITOR · ÂNGULO, UPPERCASE>",\n'
            '    "caption_top": "<≤6 palavras, com <span class=\\"hl\\">destaque</span>, sem clichê>",\n'
            '    "caption_bottom": "<1-2 frases técnicas sobre O QUE A FOTO MOSTRA (não inventar)>"\n'
            '  },\n'
        )
    if "story" in slide_types:
        schema_hint += (
            '  "story": {\n'
            '    "kicker": "<EVOLUÇÃO · LINHA ou CONTEXTO · TEMA>",\n'
            '    "headline_html": "<lowercase, ≤7 palavras, com hl>",\n'
            '    "paragraphs": ["<frase 1 fato/data>", "<frase 2 fato/spec>", "<frase 3 prova social>"]\n'
            '  },\n'
        )
    schema_hint += (
        '  "features": {\n'
        '    "kicker": "<CONSTRUÇÃO · MODELO, ex: CONSTRUÇÃO · ADIOS PRO 4>",\n'
        '    "items": [\n'
        '      {"title": "<nome técnico real, lowercase, ex: lightstrike pro / energyrods 2.0>",\n'
        '       "desc": "<1 linha c/ NÚMERO ou MATERIAL específico, sem adjetivo vazio>"},\n'
        '      ... 4 a 5 itens (omita slot se faltar dado real)\n'
        '    ]\n'
        '  },\n'
    )
    if "comparison" in slide_types:
        schema_hint += (
            '  "comparison": {\n'
            '    "kicker": "<COMPARATIVO>",\n'
            '    "headline_html": "<lowercase, hl>",\n'
            '    "left_title": "<MODELO ANTERIOR ou CONCORRENTE>",\n'
            '    "right_title": "<MODELO ATUAL>",\n'
            '    "rows": [\n'
            '      {"label": "<atributo lowercase>", "left_value": "...", "right_value": "..."},\n'
            '      ... 4 a 6 linhas\n'
            '    ]\n'
            '  },\n'
        )
    schema_hint += (
        '  "cta": {\n'
        '    "kicker": "<DISPONIBILIDADE · MERCADO>",\n'
        '    "headline_html": "<lowercase com hl, gancho de ação>",\n'
        '    "price": "<R$ X.XXX ou US$ XXX ou vazio>",\n'
        '    "availability": "<onde comprar, lowercase>"\n'
        '  }\n'
        '}'
    )

    prompt = (
        "Você é o editor-chefe da Merge — marca brasileira de wellness & endurance.\n"
        "Voz: jornalismo técnico de gadget. Lowercase Merge. pt-BR.\n"
        "NÃO É copywriter de e-commerce. NÃO é assistente. É editor com propriedade\n"
        "técnica sobre cada produto.\n\n"
        f"NOTÍCIA:\n  título: {news_title}\n  resumo: {news_summary}\n\n"
        f"ENTIDADE:\n  nome: {name}\n  marca: {brand}\n  categoria: {category}\n\n"
        f"FOTOS DISPONÍVEIS ({len(photos_meta)}):\n{photos_block}\n\n"
        f"GERE UM CARROSSEL DE {n_slides} SLIDES nos tipos: "
        f"{', '.join(slide_types)}.\n\n"
        "════ REGRA #1 — ANTI-IA / ANTI-GENERALISTA (CRÍTICO) ════\n"
        "Texto não pode ter CARA DE IA. Se o leitor desconfiar que foi LLM, falhou.\n"
        "PROIBIDO usar (banimento total):\n"
        "  • 'descubra', 'transforme', 'experimente', 'garanta o seu', 'eleve',\n"
        "    'desbloqueie', 'libere', 'conquiste', 'alcance', 'supere limites'\n"
        "  • 'design moderno', 'tecnologia avançada', 'inovador', 'revolucionário',\n"
        "    'elegante', 'sofisticado', 'premium', 'exclusivo', 'incrível',\n"
        "    'impressionante', 'extraordinário', 'perfeito para', 'feito para',\n"
        "    'ideal para', 'pensado para', 'projetado para'\n"
        "  • Frases que começam com 'Com [adjetivo]…', 'Mais que [X], é [Y]…'\n"
        "  • Listas adjetivais sem número ('leve, ágil e responsivo')\n"
        "  • Emoji (zero), exclamação (zero), pergunta retórica (zero — exceto CTA)\n"
        "  • Reescrever termo técnico em portuguesa (mantenha em inglês: stack,\n"
        "    drop, midsole, dropdown, midfoot, propulsão, etc — só quando real)\n\n"
        "════ REGRA #2 — SPECS REAIS, NÚMEROS REAIS ════\n"
        "Cada slide DEVE ter pelo menos 1 número ou nome próprio técnico (modelo de\n"
        "espuma, placa, tecido, sola, parceiro). Exemplos do vocabulário que VALE:\n"
        "  ✓ tênis: 138g, drop 6.5mm, stack 39/32.5mm, energyrods 2.0, lightstrike\n"
        "    pro, continental™, lightlock ripstop, react infinity\n"
        "  ✓ apparel: 87% poliamida / 13% elastano, 145 g/m², zíper YKK 22cm,\n"
        "    costura flatlock, malha pique birdseye\n"
        "  ✓ wetsuit: yamamoto 39 SCS, 5mm torso, costura RS2 selada\n"
        "  ✓ bike/relógio: GPS dual-band, AMOLED 1.4\", titânio grau 5, 470 lúmens\n"
        "EXTRAIA TODOS os números/nomes do resumo da notícia. Se não tiver número\n"
        "explícito no resumo nem conhecimento público sólido do produto:\n"
        "  → DEIXE vazio (\"\") ou OMITA o item. NUNCA invente número.\n"
        "  → NUNCA escreva 'aproximadamente', 'cerca de', '~' pra esconder dúvida.\n\n"
        "════ REGRA #3 — LEADS / SUBTÍTULOS ════\n"
        "Lead = EXATAMENTE 1 frase, ≤18 palavras, clickbait c/ 1 dado real.\n"
        "Tom: direto, autoral. NÃO 2 parágrafos densos. NÃO lista de specs em prosa.\n"
        "APROVADOS (use de referência):\n"
        "  ✓ 'o tênis que quebrou o recorde mundial agora calça o pelotão de elite.'\n"
        "  ✓ '138g. menos peso que um iphone.'\n"
        "  ✓ 'a 4ª geração da plataforma do WR feminino da maratona.'\n"
        "REPROVADOS (NÃO escreva nada parecido):\n"
        "  ✗ 'Quarta geração da plataforma de elite da Adidas. Lightstrike Pro,\n"
        "    EnergyRods 2.0 em carbono e cabedal Lightlock — engenharia pra manter\n"
        "    o pace negativo nos 42 km.' (longo, denso, lista em prosa)\n"
        "  ✗ 'Descubra o novo Adios Pro 4 com tecnologia revolucionária.' (clichê)\n"
        "  ✗ 'Perfeito para quem busca performance.' (vazio)\n\n"
        "════ REGRA #4 — FORMATAÇÃO ════\n"
        "  • headline_html SEMPRE lowercase. UMA expressão envolvida em\n"
        "    <span class=\"hl\">…</span> (vira laranja). Máx 8 palavras.\n"
        "  • kicker SEMPRE UPPERCASE, separado por · (bullet middot). Máx 5 palavras.\n"
        "  • features.items.title: 2-3 palavras lowercase, NOME TÉCNICO REAL.\n"
        "  • features.items.desc: 1 linha c/ número ou material específico.\n"
        "  • comparison: SÓ se houver geração anterior real ou concorrente direto\n"
        "    com dados — caso contrário OMITA o slide inteiro do JSON.\n"
        "  • cta.price: 'R$ X.XXX' (BR) ou 'US$ XXX' (global) ou \"\" se ignorar.\n"
        "  • cta.availability: lowercase, ex 'adidas.com.br · lojas autorizadas'.\n\n"
        "════ CHECKLIST FINAL ANTES DE DEVOLVER ════\n"
        "  [1] Cada slide tem pelo menos 1 número ou nome técnico próprio?\n"
        "  [2] Nenhuma palavra da lista BANIDA aparece?\n"
        "  [3] Leads têm 1 frase só, ≤18 palavras?\n"
        "  [4] Headlines ≤8 palavras, lowercase, c/ 1 <span hl>?\n"
        "  [5] Nada inventado — só dado que veio do resumo ou conhecimento sólido?\n"
        "Se algum [N] falhou, REFAÇA antes de devolver.\n\n"
        f"DEVOLVA APENAS JSON conforme schema:\n{schema_hint}"
    )

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.85,
            "responseMimeType": "application/json",
        },
    }
    url = GEMINI_URL_TMPL.format(model=GEMINI_MODEL, key=key)
    backoffs = [4, 12, 30]
    for attempt in range(len(backoffs) + 1):
        try:
            with httpx.Client(timeout=90) as c:
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
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ carousel_spec.gemini HTTP {r.status_code}: {r.text[:200]}")
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ carousel_spec.gemini exception: {e!r}")
            return None
    return None


# ----- AI-tell detector (post-generation safety net) -----

BANNED_TERMS = (
    "descubra", "transforme", "experimente", "garanta o seu", "garanta seu",
    "eleve", "desbloqueie", "libere", "conquiste", "supere limites",
    "design moderno", "tecnologia avançada", "inovador", "revolucionário",
    "elegante", "sofisticado", "premium", "exclusivo", "incrível",
    "impressionante", "extraordinário", "perfeito para", "feito para",
    "ideal para", "pensado para", "projetado para",
)


def _flag_ai_tells(copy: dict) -> list[str]:
    """Varre o JSON do Gemini e devolve lista de violações encontradas.
    Não bloqueia — só registra pra log/debug."""
    flags: list[str] = []
    blob = json.dumps(copy, ensure_ascii=False).lower()
    for term in BANNED_TERMS:
        if term in blob:
            flags.append(term)
    return flags


# ----- spec construction -----

def _decide_slide_count(n_photos: int) -> int:
    if n_photos <= 1:
        return 3
    if n_photos == 2:
        return 4
    if n_photos == 3:
        return 5
    return 6


def _safe_str(d: dict, key: str, default: str = "") -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else default


def _build_from_copy(
    copy: dict,
    photos_by_role: dict[str, Path],
    n_slides: int,
) -> ProductCarousel:
    """Monta ProductCarousel a partir do JSON do Gemini + fotos atribuídas."""
    hero = photos_by_role.get("hero")
    detail = photos_by_role.get("detail") or hero
    context = photos_by_role.get("context") or detail or hero
    top = photos_by_role.get("top") or detail or hero

    slug = _safe_str(copy, "slug") or "produto"
    slides: list = []

    cov = copy.get("cover", {}) or {}
    slides.append(CoverSlide(
        kicker=_safe_str(cov, "kicker", "PRODUTO"),
        stat_num=_safe_str(cov, "stat_num", ""),
        stat_unit=_safe_str(cov, "stat_unit", ""),
        stat_caption=_safe_str(cov, "stat_caption", ""),
        headline_html=_safe_str(cov, "headline_html", "<span class=\"hl\">novo</span>."),
        lead=_safe_str(cov, "lead", ""),
        image=hero or context or detail,  # type: ignore[arg-type]
    ))

    if n_slides >= 4:
        hp = copy.get("hero_photo", {}) or {}
        slides.append(HeroPhotoSlide(
            kicker=_safe_str(hp, "kicker", "DETALHE"),
            image=detail or hero,  # type: ignore[arg-type]
            caption_top=_safe_str(hp, "caption_top", ""),
            caption_bottom=_safe_str(hp, "caption_bottom", ""),
        ))

    if n_slides >= 5:
        st = copy.get("story", {}) or {}
        paragraphs = st.get("paragraphs") or []
        if not isinstance(paragraphs, list):
            paragraphs = []
        slides.append(StorySlide(
            kicker=_safe_str(st, "kicker", "CONTEXTO"),
            headline_html=_safe_str(st, "headline_html", ""),
            paragraphs=[str(p) for p in paragraphs if p],
            image=context or top or hero,  # type: ignore[arg-type]
            image_side="right",
        ))

    feat = copy.get("features", {}) or {}
    items = feat.get("items") or []
    feature_objs: list[Feature] = []
    for it in items[:5]:
        if isinstance(it, dict):
            t = _safe_str(it, "title", "")
            d = _safe_str(it, "desc", "")
            if t or d:
                feature_objs.append(Feature(t, d))
    slides.append(FeaturesSlide(
        kicker=_safe_str(feat, "kicker", "CONSTRUÇÃO"),
        image=top or detail or hero,  # type: ignore[arg-type]
        features=feature_objs,
    ))

    if n_slides >= 6:
        cmp_ = copy.get("comparison")
        if isinstance(cmp_, dict) and cmp_.get("rows"):
            rows_raw = cmp_.get("rows") or []
            rows = [
                ComparisonRow(
                    _safe_str(r, "label", ""),
                    _safe_str(r, "left_value", ""),
                    _safe_str(r, "right_value", ""),
                )
                for r in rows_raw
                if isinstance(r, dict)
            ]
            if rows:
                slides.append(ComparisonSlide(
                    kicker=_safe_str(cmp_, "kicker", "COMPARATIVO"),
                    headline_html=_safe_str(cmp_, "headline_html", ""),
                    left_title=_safe_str(cmp_, "left_title", ""),
                    right_title=_safe_str(cmp_, "right_title", ""),
                    rows=rows,
                ))

    cta_ = copy.get("cta", {}) or {}
    slides.append(CTASlide(
        kicker=_safe_str(cta_, "kicker", "DISPONIBILIDADE"),
        headline_html=_safe_str(cta_, "headline_html", "vai <span class=\"hl\">testar</span>?"),
        price=_safe_str(cta_, "price", ""),
        availability=_safe_str(cta_, "availability", ""),
        image=hero or detail,
    ))

    return ProductCarousel(slug=slug, slides=slides)


def _fallback_spec(
    entity: dict,
    photos_by_role: dict[str, Path],
    news_title: str,
    news_summary: str,
) -> ProductCarousel:
    """Spec mínima 3-slide quando Gemini falha."""
    name = entity.get("name", "produto")
    brand = entity.get("brand") or ""
    category = entity.get("category") or "produto"
    kicker = f"{category.upper()} · {brand.upper()} {name.upper()}".strip(" ·")
    short_summary = (news_summary or news_title)[:280]
    hero = photos_by_role.get("hero") or photos_by_role.get("detail")

    return ProductCarousel(
        slug=_slug(name),
        slides=[
            CoverSlide(
                kicker=kicker,
                stat_num="",
                stat_unit="",
                stat_caption="",
                headline_html=f"{name.lower()}: <span class=\"hl\">novidade</span>.",
                lead=short_summary,
                image=hero,  # type: ignore[arg-type]
            ),
            FeaturesSlide(
                kicker="DESTAQUES",
                image=photos_by_role.get("top") or hero,  # type: ignore[arg-type]
                features=[Feature("ficha", short_summary[:140])],
            ),
            CTASlide(
                kicker="MAIS INFO",
                headline_html="quer <span class=\"hl\">saber mais</span>?",
                price="",
                availability=(brand or "site oficial").lower(),
                image=hero,
            ),
        ],
    )


def build_carousel_spec(
    entity: dict,
    photos: list[dict],
    news_title: str,
    news_summary: str,
) -> Optional[ProductCarousel]:
    """Função principal: entity + photos + summary → ProductCarousel.

    Retorna None se não houver foto utilizável.
    """
    if not photos:
        return None
    photos_by_role = _assign_photos(photos)
    if not photos_by_role.get("hero"):
        return None

    n_slides = _decide_slide_count(len(photos))
    copy = _gemini_generate_copy(
        entity, photos, news_title, news_summary, n_slides
    )
    if copy:
        flags = _flag_ai_tells(copy)
        if flags:
            print(f"⚠ carousel_spec: AI-tells detectados ({len(flags)}): {flags}")
        try:
            return _build_from_copy(copy, photos_by_role, n_slides)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ carousel_spec: build_from_copy falhou: {e!r}")
    return _fallback_spec(entity, photos_by_role, news_title, news_summary)


if __name__ == "__main__":
    # Smoke test offline com fotos curated do Adios Pro 4
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    BRAND = ROOT / "brand" / "images"
    fake_entity = {
        "kind": "product",
        "name": "Adizero Adios Pro 4",
        "brand": "Adidas",
        "category": "running shoe",
        "confidence": 0.95,
    }
    fake_photos = [
        {
            "path": BRAND / "AdiosPro4_hero.jpg",
            "vision": {"shot_type": "hero", "angle": "lateral",
                       "notes": "tênis roxo c/ listras laranja, vista lateral"},
            "url": "test",
        },
        {
            "path": BRAND / "AdiosPro4_side.jpg",
            "vision": {"shot_type": "detail", "angle": "top-down",
                       "notes": "vista superior do cabedal"},
            "url": "test",
        },
    ]
    spec = build_carousel_spec(
        fake_entity,
        fake_photos,
        news_title="Adidas lança Adizero Adios Pro 4 com 138g",
        news_summary=(
            "A Adidas apresentou o Adios Pro 4, quarta geração do tênis de "
            "maratona de elite. O modelo pesa 138g (tamanho 9 US), tem espuma "
            "Lightstrike Pro, EnergyRods 2.0 em carbono e cabedal Lightlock "
            "ripstop. Drop de 6.5mm. Preço sugerido: R$ 2.499 no Brasil."
        ),
    )
    if not spec:
        print("FALHOU: spec None")
        sys.exit(1)
    print(f"\nslug: {spec.slug}")
    print(f"slides: {len(spec.slides)}")
    for s in spec.slides:
        print(f"  [{s.kind}] kicker={s.kicker!r}")
        if hasattr(s, "headline_html"):
            print(f"     headline: {s.headline_html!r}")
