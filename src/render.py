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

import json
import re
import sys
from pathlib import Path

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
    bg_rel = base_vars.get("BG_IMAGE", "")
    if bg_rel:
        bg_path = (ROOT / "brand" / "images" / bg_rel).resolve()
        bg_image_url = bg_path.as_uri()
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
    """Renderiza feed (1080x1350) e story (1080x1920) de um brief."""
    written = []
    for size, w, h, out_dir in [
        ("feed", 1080, 1350, OUT_FEED),
        ("story", 1080, 1920, OUT_STORY),
    ]:
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
