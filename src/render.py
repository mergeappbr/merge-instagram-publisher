"""
Merge Creator — renderer HTML → PNG.

Pipeline:
  1. Lê briefs/*.json
  2. Para cada brief: monta HTML (base + template + vars) em duas variantes (feed 1080x1350 e story 1080x1920)
  3. Renderiza com Playwright/Chromium e exporta PNG em output/{feed,stories}/

Uso:
  python src/render.py                 # renderiza todos os briefs
  python src/render.py 03_quiz_triatlo # renderiza um brief específico
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
BRIEFS = ROOT / "content" / "briefs"
OUT_FEED = ROOT / "output" / "feed"
OUT_STORY = ROOT / "output" / "stories"
PARTIALS = ROOT / "brand" / "_partials.html"

LOGO_SVG, ARROW_SVG = "", ""


def load_partials() -> None:
    """Extrai os SVGs inline do _partials.html."""
    global LOGO_SVG, ARROW_SVG
    raw = PARTIALS.read_text(encoding="utf-8")
    blocks = re.split(r"<!--\s*@(\w+)\s*-->", raw)
    # blocks: ['', 'LOGO_SVG', '<svg>...</svg>', 'ARROW_SVG', '<svg>...</svg>']
    parts = dict(zip(blocks[1::2], blocks[2::2]))
    LOGO_SVG = parts["LOGO_SVG"].strip()
    ARROW_SVG = parts["ARROW_SVG"].strip()


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _mime_from_bytes(b: bytes) -> str:
    """Detecta MIME por magic bytes. Default: jpeg."""
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _bytes_to_data_uri(img_bytes: bytes) -> str:
    mime = _mime_from_bytes(img_bytes)
    b64 = base64.b64encode(img_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _absolute_bg_to_data_uri(url: str) -> str | None:
    """Converte http(s)://, file://, ou data: BG_IMAGE em data URI inline.

    Garantia: imagem fica no HTML, sem depender de Chromium carregar
    file:// (sandbox restrito) ou rede externa (timeouts) durante render.
    Retorna None em falha — caller mantém URL original como fallback.
    """
    if url.startswith("data:"):
        return url
    if url.startswith("file://"):
        try:
            parsed = urlparse(url)
            local_path = Path(unquote(parsed.path))
            if not local_path.is_file():
                print(f"⚠ render.bg: file:// não existe: {local_path}")
                return None
            return _bytes_to_data_uri(local_path.read_bytes())
        except Exception as e:  # noqa: BLE001
            print(f"⚠ render.bg: file:// erro {e!r}")
            return None
    if url.startswith(("http://", "https://")):
        try:
            import httpx  # local import: render também roda standalone
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                r = client.get(url)
                if r.status_code != 200:
                    print(f"⚠ render.bg: HTTP {r.status_code} em {url[:80]}")
                    return None
                return _bytes_to_data_uri(r.content)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ render.bg: http erro {e!r}")
            return None
    return None


def _resolve_image_path(rel: str) -> Path | None:
    """Resolve BG_IMAGE para um arquivo de imagem real.

    Aceita:
      - Caminho relativo direto a brand/images/ (ex: 'marathon.jpg')
      - Pasta direta em brand/images/ (ex: 'comparativos/foo.jpg' OU 'comparativos')
      - Slug do banco em brand/images/_bank/ (ex: 'marathon_finish_line' →
        procura em brand/images/_bank/marathon_finish_line/ pegando primeira
        imagem alfabeticamente — preferindo subpasta 'unsplash' se existir)

    Retorna None se não achar arquivo válido — caller deixa BG vazio (sem crash).
    """
    candidates: list[Path] = [
        ROOT / "brand" / "images" / rel,
        ROOT / "brand" / "images" / "_bank" / rel,  # slug do banco direto
    ]
    for p in candidates:
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            return p
        if p.is_dir():
            # 'ai/' tem prioridade (foto gerada deliberadamente p/ esse slug),
            # depois stock 'unsplash' / 'pexels', por fim raiz do slug.
            for sub in ("ai", "unsplash", "pexels", ""):
                d = p / sub if sub else p
                if d.is_dir():
                    files = sorted(
                        f for f in d.iterdir()
                        if f.is_file() and f.suffix.lower() in _IMAGE_EXTS
                    )
                    if files:
                        return files[0]
    return None


def build_html(brief: dict, size: str) -> str:
    """Monta HTML final para um brief em determinado tamanho ('feed' ou 'story')."""
    template_name = brief["template"]
    template = (TEMPLATES / f"{template_name}.html").read_text(encoding="utf-8")
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")

    # Resolve placeholders de tamanho/escala
    is_story = size == "story"
    # Detecta range no STAT_NUM (ex.: "50–60", "4–6") pra shrinkar a fonte
    stat_num_str = str(brief.get("vars", {}).get("STAT_NUM", ""))
    is_range = ("–" in stat_num_str) or ("-" in stat_num_str and len(stat_num_str) >= 4)

    stat_classes = []
    if is_story:
        stat_classes.append("bigstat--story")
    if is_range:
        stat_classes.append("bigstat--compact")

    size_classes = {
        "SIZE": "story" if is_story else "feed",
        "KICKER_CLASS": "kicker--story" if is_story else "",
        "HEADLINE_SIZE_CLASS": "headline--story" if is_story else "",
        "LEAD_SIZE_CLASS": "lead--story" if is_story else "",
        "STAT_SIZE_CLASS": " ".join(stat_classes),
        "FOOTER_CLASS": "footer--story" if is_story else "",
        "HERO_SIZE_CLASS": "hero--story" if is_story else "",
        "LOGO_CLASS": "brand-logo--story" if is_story else "",
    }

    # Logo e arrow inline (com classe contextual)
    logo_html = LOGO_SVG.replace("{{LOGO_CLASS}}", size_classes["LOGO_CLASS"])
    arrow_html = f'<span class="cta-arrow">{ARROW_SVG}</span>'

    # Mescla vars base com story_vars (se size==story) — permite composição dedicada para stories
    base_vars = dict(brief.get("vars", {}))
    if is_story:
        base_vars.update(brief.get("story_vars", {}))

    # Resolve background image (caminho relativo a brand/images/ → file:// URL)
    # Aceita: arquivo direto ('marathon.jpg'), pasta direta ('comparativos'), OU
    # slug do _bank ('marathon_finish_line' → procura em _bank/<slug>/).
    bg_rel = base_vars.get("BG_IMAGE", "")
    if bg_rel:
        # URL absoluta (http/https/file/data) já resolvida — ex: visual.resolve_bg_for_news
        # devolveu URL pública do R2 ou file:// do cache local. Convertemos pra
        # data URI inline pra evitar problemas de file:// + sandbox no Chromium.
        if bg_rel.startswith(("http://", "https://", "file://", "data:")):
            data_uri = _absolute_bg_to_data_uri(bg_rel)
            bg_image_url = data_uri or bg_rel  # fallback: usa URL como veio
        else:
            bg_path = _resolve_image_path(bg_rel)
            bg_image_url = bg_path.as_uri() if bg_path else ""
    else:
        bg_image_url = ""

    # Resolve screen image (mockup) — mesma lógica
    screen_rel = base_vars.get("SCREEN_IMAGE", "")
    if screen_rel:
        screen_path = (ROOT / "brand" / "images" / screen_rel).resolve()
        screen_image_url = screen_path.as_uri()
    else:
        screen_image_url = ""

    # Resolve product image (product card) — mesma lógica
    product_rel = base_vars.get("PRODUCT_IMAGE", "")
    if product_rel:
        product_path = (ROOT / "brand" / "images" / product_rel).resolve()
        product_image_url = product_path.as_uri()
    else:
        product_image_url = ""

    # Resolve event logo (logo de evento/parceiro) — resolvido de brand/ direto
    # (não brand/images/), pois logos institucionais ficam na raiz do brand kit.
    event_logo_rel = base_vars.get("EVENT_LOGO", "")
    if event_logo_rel:
        event_logo_path = (ROOT / "brand" / event_logo_rel).resolve()
        event_logo_url = event_logo_path.as_uri()
    else:
        event_logo_url = ""

    # Variantes de overlay e posição
    overlay = base_vars.get("OVERLAY", "")  # "", "side", "bottom"
    overlay_class = f"bg-overlay--{overlay}" if overlay in ("side", "bottom") else ""
    bg_position = base_vars.get("BG_POSITION", "")
    bg_position_style = f"background-position:{bg_position};" if bg_position else ""

    # Camadas de fundo: só inseridas quando BG_IMAGE é definido
    if bg_image_url:
        bg_class = "canvas--has-bg"
        bg_layers = (
            f'<div class="bg-image" style="background-image:url(\'{bg_image_url}\'); {bg_position_style}"></div>'
            f'<div class="bg-overlay {overlay_class}"></div>'
        )
    else:
        bg_class = ""
        bg_layers = ""

    # Kicker do stat (opcional) — label curto acima do numeral (ex.: "DROP")
    stat_kicker = base_vars.get("STAT_KICKER", "")
    if stat_kicker:
        kicker_class = "bigstat-kicker--story" if is_story else ""
        stat_kicker_html = f'<div class="bigstat-kicker {kicker_class}">{stat_kicker}</div>'
    else:
        stat_kicker_html = ""

    # Bloco de preço (opcional) — só renderiza se PRICE for definido no brief
    price_value = base_vars.get("PRICE", "")
    if price_value:
        price_class = "price-tag--story" if is_story else ""
        price_tag_html = (
            f'<div class="price-tag {price_class}">'
            f'<span class="price-tag__label">preço médio</span>'
            f'<span class="price-tag__value">{price_value}</span>'
            f'</div>'
        )
    else:
        price_tag_html = ""

    # Conjunto completo de variáveis
    vars_all = {
        **size_classes,
        **base_vars,
        "LOGO": logo_html,
        "ARROW": arrow_html,
        "PRICE_TAG": price_tag_html,
        "STAT_KICKER_HTML": stat_kicker_html,
        "BG_IMAGE": bg_image_url,
        "SCREEN_IMAGE": screen_image_url,
        "PRODUCT_IMAGE": product_image_url,
        "EVENT_LOGO": event_logo_url,
        "OVERLAY_VARIANT": overlay_class,
        "BG_POSITION_STYLE": bg_position_style,
        "BG_CLASS": bg_class,
        "BG_LAYERS": bg_layers,
        # Defaults para placeholders opcionais (evita {{VAR}} sobrando)
        "META": base_vars.get("META", ""),
        "CTA_LABEL": base_vars.get("CTA_LABEL", "saiba mais"),
        "PILL": base_vars.get("PILL", ""),
        "KICKER": base_vars.get("KICKER", ""),
        "HEADLINE": base_vars.get("HEADLINE", ""),
        "LEAD": base_vars.get("LEAD", ""),
        "STORY_HINT": base_vars.get("STORY_HINT", "") if is_story else "",
    }

    content = template
    for key, value in vars_all.items():
        content = content.replace("{{" + key + "}}", str(value))

    # Limpa quaisquer {{...}} remanescentes
    content = re.sub(r"\{\{[A-Z_]+\}\}", "", content)

    title = brief.get("title", brief["id"])
    html = base.replace("{{TITLE}}", title).replace("{{CONTENT}}", content)
    return html


def render_brief(page, brief: dict) -> list[Path]:
    """Renderiza feed (1080x1350) e story (1080x1920) de um brief.

    Quizzes saem só em feed: o sticker de enquete do Instagram não pode ser
    adicionado via API, então a versão story sem ele não tem utilidade real.
    """
    written = []
    sizes = [("feed", 1080, 1350, OUT_FEED)]
    if brief.get("template") != "quiz":
        sizes.append(("story", 1080, 1920, OUT_STORY))
    for size, w, h, out_dir in sizes:
        html = build_html(brief, size)

        # Salva HTML temporário pra debug e pra Playwright carregar com fontes do Google
        tmp_html = ROOT / "output" / f".tmp_{brief['id']}_{size}.html"
        tmp_html.write_text(html, encoding="utf-8")

        page.set_viewport_size({"width": w, "height": h})
        page.goto(tmp_html.as_uri(), wait_until="networkidle")
        # Garante carregamento das webfonts
        page.evaluate("document.fonts.ready")

        out = out_dir / f"{brief['id']}.png"
        page.screenshot(path=str(out), omit_background=False, full_page=False, clip={"x": 0, "y": 0, "width": w, "height": h})
        written.append(out)
        tmp_html.unlink(missing_ok=True)
    return written


def main(argv: list[str]) -> int:
    load_partials()

    if argv:
        targets = [BRIEFS / f"{argv[0]}.json"]
    else:
        targets = sorted(BRIEFS.glob("*.json"))

    if not targets:
        print("Nenhum brief encontrado em content/briefs/")
        return 1

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(device_scale_factor=1)
        page = ctx.new_page()

        for path in targets:
            brief = json.loads(path.read_text(encoding="utf-8"))
            print(f"→ {brief['id']} ({brief['template']})")
            for out in render_brief(page, brief):
                print(f"   ✓ {out.relative_to(ROOT)}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
