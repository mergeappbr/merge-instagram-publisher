"""Carrossel comparativo: 4 Garmin Forerunner AMOLED (165 / 265 / 570 / 970).

5 slides 1080×1350, tema cream, com press shots oficiais Garmin (res.garmin.com).
Layout denso: grid 2-col cover, 2x2 lineup com hero por modelo, spec sheet com
thumbnails de header, use-case com thumb por linha, CTA split com 970 dominante.

Press shots baixados em brand/garmin/ (cf-lg.jpg da loja oficial Garmin US).

Output: output/posts/forerunner_amoled/01_cover.png ... 05_cta.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_STYLES = (ROOT / "templates" / "posts" / "_post_styles.css").read_text(encoding="utf-8")

W, H = 1080, 1350
SLUG = "forerunner_amoled"
OUT = ROOT / "output" / "posts" / SLUG
TMP = ROOT / ".tmp_posts" / SLUG
BRAND_DIR = ROOT / "brand" / "garmin"

WHITE_THRESHOLD = 240
TOLERANCE = 18


# ---------- IMG PREP ----------

def strip_white_bg(src: Path, dest: Path) -> Path:
    """Mesma lógica do product_post._strip_white_bg, simplificada inline."""
    img = Image.open(src).convert("RGBA")
    pixels = img.load()
    w, h = img.size
    samples = [
        pixels[2, 2], pixels[w // 2, 2], pixels[w - 3, 2],
        pixels[2, h // 2], pixels[w - 3, h // 2],
        pixels[2, h - 3], pixels[w // 2, h - 3], pixels[w - 3, h - 3],
    ]
    rs = [s[0] for s in samples]
    gs = [s[1] for s in samples]
    bs = [s[2] for s in samples]
    spread = max(max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs))
    bg = None
    if spread < 10:
        bg = (sum(rs) // len(rs), sum(gs) // len(gs), sum(bs) // len(bs))
    for y in range(h):
        for x in range(w):
            r, g, b, _ = pixels[x, y]
            if r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD:
                pixels[x, y] = (r, g, b, 0)
                continue
            if bg is not None:
                if (abs(r - bg[0]) <= TOLERANCE
                        and abs(g - bg[1]) <= TOLERANCE
                        and abs(b - bg[2]) <= TOLERANCE):
                    pixels[x, y] = (r, g, b, 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


# ---------- CSS ----------

CSS = """
.fr-root { display:flex; flex-direction:column; height:100%; }

/* watermark footer */
.fr-foot { margin-top:auto; display:flex; align-items:center; justify-content:space-between; }
.fr-foot .mark {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:38px; letter-spacing:-0.025em;
  text-transform:lowercase; color: var(--ink-dark);
}
.fr-foot .mark .dot { color: var(--orange); }
.fr-foot .page {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:15px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.55;
}

/* ===== COVER (single-flow, watch centralizado, sem box) ===== */
.fr-cover-head { display:flex; flex-direction:column; gap:0; }
.fr-cover-title {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:118px; line-height:0.86; letter-spacing:-0.055em;
  margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.fr-cover-title .hl { color: var(--orange); }
.fr-cover-cap {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:14px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.78;
  margin-top:16px;
}
.fr-cover-sub {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size:36px; line-height:1.05; letter-spacing:-0.025em;
  margin:12px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.fr-cover-sub .hl { color: var(--orange); }
.fr-cover-lead {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:16px; line-height:1.55; color: var(--ink-dark);
  margin:14px 0 0; max-width: 880px;
}

/* hero: 2-col com watch dominante + tagline preço */
.fr-cover-hero {
  flex:1; display:grid; grid-template-columns: 1.1fr 1fr;
  align-items:center; gap:30px; margin-top:18px; min-height:0;
}
.fr-cover-hero .watch {
  display:flex; align-items:center; justify-content:center; height:100%;
}
.fr-cover-hero .watch img { max-width:100%; max-height:100%; object-fit:contain; }
.fr-cover-hero .tag {
  display:flex; flex-direction:column; gap:6px;
}
.fr-cover-hero .tag .pill {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:11px; letter-spacing:0.18em; text-transform:uppercase;
  padding:6px 11px; background:var(--orange); color:var(--ink-dark);
  width:fit-content; margin-bottom:6px;
}
.fr-cover-hero .tag .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:56px; line-height:0.92; letter-spacing:-0.04em;
  color: var(--ink-dark); text-transform:lowercase;
}
.fr-cover-hero .tag .model .hl { color: var(--orange); }
.fr-cover-hero .tag .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:62px; line-height:1; letter-spacing:-0.045em;
  color: var(--orange); margin-top:4px;
}
.fr-cover-hero .tag .sub {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:12px; letter-spacing:0.14em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.7; margin-top:10px;
  max-width: 340px;
}

/* escada de preços (full-width strip antes do footer) */
.fr-cover-ladder {
  margin-top:18px; padding-top:14px;
  display:grid; grid-template-columns: repeat(4, 1fr); gap:18px;
  border-top: 2px solid rgba(10,10,10,0.14);
}
.fr-cover-ladder .col {
  display:flex; flex-direction:column; gap:4px;
  padding-right:14px;
  border-right: 1px solid rgba(10,10,10,0.10);
}
.fr-cover-ladder .col:last-child { border-right:0; padding-right:0; }
.fr-cover-ladder .mdl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:28px; letter-spacing:-0.025em;
  text-transform:lowercase; color: var(--ink-dark); line-height:1;
}
.fr-cover-ladder .mdl .hl { color: var(--orange); }
.fr-cover-ladder .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:22px; letter-spacing:-0.02em; color: var(--orange); line-height:1;
  margin-top:2px;
}
.fr-cover-ladder .tag {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:10.5px; letter-spacing:0.16em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.6; margin-top:4px;
}

/* ===== LINEUP (2x2 grid com hero por card) ===== */
.fr-cmp-headline {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:56px; line-height:1.0; letter-spacing:-0.035em;
  margin:12px 0 18px; text-transform:lowercase; color: var(--ink-dark);
}
.fr-cmp-headline .hl { color: var(--orange); }
.fr-lineup {
  flex:1; display:grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr;
  gap:20px; margin-top:6px; margin-bottom:14px; min-height:0;
}
.fr-card {
  display:grid; grid-template-rows: 1fr auto;
  background: rgba(10,10,10,0.035);
  border: 1.5px solid rgba(10,10,10,0.10);
  overflow:hidden; min-height:0;
}
.fr-card-img {
  display:flex; align-items:center; justify-content:center;
  padding:14px; min-height:0;
}
.fr-card-img img { max-width: 100%; max-height: 100%; object-fit: contain; }
.fr-card-body { padding:16px 20px 18px; border-top:1.5px solid rgba(10,10,10,0.10); }
.fr-card-head {
  display:flex; align-items:baseline; justify-content:space-between; gap:10px;
  margin-bottom:6px;
}
.fr-card .tag {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:11px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.fr-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:42px; line-height:0.92; letter-spacing:-0.03em;
  color: var(--ink-dark); text-transform:lowercase;
}
.fr-card .model .hl { color: var(--orange); }
.fr-card .pitch {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:12.5px; line-height:1.45; color: var(--ink-dark-soft);
  margin:6px 0 0;
}
.fr-card .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:28px; letter-spacing:-0.025em; color: var(--orange);
  margin-top:8px;
}

/* ===== COMPARE (spec sheet com thumb header) ===== */
.fr-cmp-thumbs {
  display:grid; grid-template-columns: 1.05fr 0.9fr 0.9fr 0.9fr 0.9fr;
  align-items:end; gap:0; padding-bottom:10px;
  border-bottom: 2px solid rgba(10,10,10,0.14);
}
.fr-cmp-thumb { display:flex; flex-direction:column; align-items:center; gap:4px; }
.fr-cmp-thumb img { width: 92px; height: 92px; object-fit: contain; }
.fr-cmp-thumb .lbl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:20px; letter-spacing:-0.02em; color: var(--ink-dark);
  text-transform:lowercase;
}
.fr-cmp-table {
  flex:1; display:flex; flex-direction:column; min-height:0; margin-top:0;
}
.fr-cmp-row {
  flex:1;
  display:grid; grid-template-columns: 1.05fr 0.9fr 0.9fr 0.9fr 0.9fr;
  align-items:center;
  padding:10px 0;
  border-bottom: 1px solid rgba(10,10,10,0.08);
}
.fr-cmp-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:12px; letter-spacing:0.16em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.fr-cmp-row .val {
  font-family:'Inter', sans-serif; font-weight:700;
  font-size:17px; letter-spacing:-0.01em;
  color: var(--ink-dark); line-height:1.2; text-align:center;
}
.fr-cmp-row .val.is-hl { color: var(--orange); font-weight:900; }

/* ===== USE-CASE (4 rows flex:1 com thumb) ===== */
.fr-uc-grid {
  flex:1; display:flex; flex-direction:column; gap:14px;
  margin-top:8px; margin-bottom:8px; min-height:0;
}
.fr-uc-card {
  flex:1; display:grid; grid-template-columns: 130px 1fr;
  gap:22px; align-items:center;
  padding:14px 22px;
  background: rgba(10,10,10,0.035);
  border-left: 5px solid var(--orange);
  min-height:0;
}
.fr-uc-card .thumb-wrap {
  display:flex; align-items:center; justify-content:center;
  height:100%;
}
.fr-uc-card .thumb-wrap img { max-width:100%; max-height:130px; object-fit:contain; }
.fr-uc-text { display:flex; flex-direction:column; gap:4px; }
.fr-uc-row1 {
  display:flex; align-items:baseline; gap:14px;
}
.fr-uc-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:32px; letter-spacing:-0.03em; color: var(--orange);
  line-height:1; text-transform:lowercase;
}
.fr-uc-card .who {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size:22px; letter-spacing:-0.015em; color: var(--ink-dark);
  line-height:1.15; text-transform:lowercase;
}
.fr-uc-card .why {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:13.5px; line-height:1.5; color: var(--ink-dark-soft);
}

/* ===== CTA (split 2-col) ===== */
.fr-cta-grid {
  flex:1; display:grid; grid-template-columns: 1.1fr 0.9fr;
  gap:28px; margin-top:18px; min-height:0;
}
.fr-cta-left { display:flex; flex-direction:column; }
.fr-cta-headline {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:84px; line-height:0.94; letter-spacing:-0.045em;
  margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.fr-cta-headline .hl { color: var(--orange); }
.fr-cta-band {
  flex:1; margin-top:26px;
  display:flex; flex-direction:column;
  justify-content: space-between; gap:8px;
}
.fr-cta-row {
  display:flex; justify-content:space-between; align-items:baseline;
  padding-bottom:14px;
  border-bottom: 1px solid rgba(10,10,10,0.12);
}
.fr-cta-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:13px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.7;
}
.fr-cta-row .val {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:24px; letter-spacing:-0.025em; color: var(--orange);
}
.fr-cta-where {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:13.5px; line-height:1.55; color: var(--ink-dark); opacity:0.78;
  margin-top:18px;
}
.fr-cta-right {
  display:flex; flex-direction:column;
  background: rgba(10,10,10,0.04);
  border: 1.5px solid rgba(10,10,10,0.10);
  position:relative; overflow:hidden;
}
.fr-cta-right .badge {
  position:absolute; top:14px; left:14px; z-index:2;
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:11px; letter-spacing:0.18em; text-transform:uppercase;
  padding:7px 11px; background:var(--orange); color:var(--ink-dark);
}
.fr-cta-right .stage {
  flex:1; display:flex; align-items:center; justify-content:center;
  padding:18px; min-height:0;
}
.fr-cta-right .stage img { max-width:100%; max-height:100%; object-fit:contain; }
.fr-cta-right .caption {
  padding:14px 18px; border-top:1.5px solid rgba(10,10,10,0.10);
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:13px; letter-spacing:0.06em; color: var(--ink-dark);
  display:flex; justify-content:space-between; align-items:baseline;
}
.fr-cta-right .caption .big {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:22px; letter-spacing:-0.02em; color: var(--orange);
}
"""


def _shell(body: str) -> str:
    return (
        '<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><style>'
        f"{BASE_STYLES}{CSS}"
        f"</style></head><body>{body}</body></html>"
    )


def _foot(page: str) -> str:
    return (
        '<div class="fr-foot">'
        '<span class="mark">merge<span class="dot">.</span></span>'
        f'<span class="page">{page}</span>'
        '</div>'
    )


# ---------- SLIDES ----------

def slide_01_cover(img970: Path) -> str:
    body = f"""
<div class="post post--light">
  <div class="post__content fr-root">
    <span class="kicker"><span class="dot"></span>GARMIN · LINHA AMOLED · 2025</span>
    <div class="fr-cover-head">
      <h1 class="fr-cover-title">garmin<br><span class="hl">forerunners</span>.</h1>
      <span class="fr-cover-cap">4 amoled ativos · 165 / 265 / 570 / 970</span>
      <h2 class="fr-cover-sub">qual é o <span class="hl">seu</span>?</h2>
      <p class="fr-cover-lead">
        De R$ 2.499 a R$ 8.999. AMOLED em todos, mas diferenças reais em
        GPS multi-banda, ECG, mapas topográficos e autonomia.
      </p>
    </div>
    <div class="fr-cover-hero">
      <div class="watch"><img src="file://{img970}" alt="forerunner 970"></div>
      <div class="tag">
        <span class="pill">topo de linha</span>
        <div class="model">fr <span class="hl">970</span></div>
        <div class="price">r$ 8.999</div>
        <div class="sub">sapphire · led · mapas topo · 15d bateria</div>
      </div>
    </div>
    <div class="fr-cover-ladder">
      <div class="col">
        <span class="mdl">fr <span class="hl">165</span></span>
        <span class="price">r$ 2.499</span>
        <span class="tag">entry · 2024</span>
      </div>
      <div class="col">
        <span class="mdl">fr <span class="hl">265</span></span>
        <span class="price">r$ 4.499</span>
        <span class="tag">mid · 2023</span>
      </div>
      <div class="col">
        <span class="mdl">fr <span class="hl">570</span></span>
        <span class="price">r$ 6.499</span>
        <span class="tag">pro · 2025</span>
      </div>
      <div class="col">
        <span class="mdl">fr <span class="hl">970</span></span>
        <span class="price">r$ 8.999</span>
        <span class="tag">flagship · 2025</span>
      </div>
    </div>
    {_foot("01 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_02_lineup(imgs: dict[str, Path]) -> str:
    cards = [
        ("165", "ENTRY · 2024",    "GPS single-band · 11d · 39g · sem ECG, sem mapas", "R$ 2.499"),
        ("265", "MID · 2023",      "multi-band · 13d · 47g · training readiness",      "R$ 4.499"),
        ("570", "PRO · 2025",      "ECG · speaker+mic · multi-band satiq · 11d",       "R$ 6.499"),
        ("970", "FLAGSHIP · 2025", "sapphire · lanterna led · mapas topo · 15d",       "R$ 8.999"),
    ]
    cards_html = "".join(
        f'''<div class="fr-card">
              <div class="fr-card-img"><img src="file://{imgs[m]}" alt="fr {m}"></div>
              <div class="fr-card-body">
                <div class="fr-card-head">
                  <span class="model">fr<span class="hl">{m}</span></span>
                  <span class="tag">{tag}</span>
                </div>
                <p class="pitch">{pitch}</p>
                <div class="price">{price}</div>
              </div>
            </div>'''
        for m, tag, pitch, price in cards
    )
    body = f"""
<div class="post post--light">
  <div class="post__content fr-root">
    <span class="kicker"><span class="dot"></span>LINHA AMOLED · LINEUP</span>
    <h2 class="fr-cmp-headline">os <span class="hl">4 amoled</span> da garmin agora.</h2>
    <div class="fr-lineup">{cards_html}</div>
    {_foot("02 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_03_compare(imgs: dict[str, Path]) -> str:
    rows = [
        ("tela",     ["1.2\" amoled", "1.3\" amoled", "1.2/1.4\"", "1.4\" amoled"], False),
        ("cristal",  ["gorilla 3",    "gorilla 3",    "gorilla 3", "sapphire"],     True),
        ("gps",      ["single-band",  "multi-band",   "satiq",     "satiq"],        False),
        ("bateria",  ["11d / 19h",    "13d / 20h",    "11d / 23h", "15d / 21h"],    False),
        ("peso",     ["39 g",         "47 g",         "50 g",      "56 g"],         False),
        ("ecg",      ["—",            "—",            "✓",         "✓"],            False),
        ("lanterna", ["—",            "—",            "—",         "✓ led"],        True),
        ("mapas",    ["—",            "—",            "—",         "topo"],         True),
        ("preço br", ["r$ 2.499",     "r$ 4.499",     "r$ 6.499",  "r$ 8.999"],     False),
    ]
    rows_html = ""
    for label, vals, hl_last in rows:
        cells = "".join(
            f'<span class="val{" is-hl" if (hl_last and i == 3) else ""}">{v}</span>'
            for i, v in enumerate(vals)
        )
        rows_html += f'<div class="fr-cmp-row"><span class="lbl">{label}</span>{cells}</div>'

    thumbs = "".join(
        f'<div class="fr-cmp-thumb"><img src="file://{imgs[m]}" alt="fr {m}"><span class="lbl">fr {m}</span></div>'
        for m in ["165", "265", "570", "970"]
    )
    body = f"""
<div class="post post--light">
  <div class="post__content fr-root">
    <span class="kicker"><span class="dot"></span>SPEC SHEET · COMPARATIVO TÉCNICO</span>
    <h2 class="fr-cmp-headline">onde <span class="hl">cada um</span> ganha.</h2>
    <div class="fr-cmp-thumbs">
      <span></span>
      {thumbs}
    </div>
    <div class="fr-cmp-table">
      {rows_html}
    </div>
    {_foot("03 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_04_usecase(imgs: dict[str, Path]) -> str:
    cards = [
        ("165", "primeiro relógio sério",
         "5k a meia-maratona · quem chegou agora no treino estruturado e quer dados de sono e prontidão sem pagar multi-band."),
        ("265", "ponto-doce custo-benefício",
         "maratonista amador · multi-band em túneis e prédios + training readiness é o salto real que justifica os r$ 2.000 sobre o 165."),
        ("570", "saúde + treino sem mapas",
         "quem treina forte mas valoriza ECG, atender ligação no pulso e quer satiq dual-band — sem precisar carregar mapa topográfico."),
        ("970", "ultra, trail e quem vive com o relógio",
         "trail runner, ironmaniaco ou quem precisa de mapas topo, lanterna pra correr de madrugada e sapphire pra durar 5+ anos."),
    ]
    cards_html = "".join(
        f'''<div class="fr-uc-card">
              <div class="thumb-wrap"><img src="file://{imgs[m]}" alt="fr {m}"></div>
              <div class="fr-uc-text">
                <div class="fr-uc-row1">
                  <span class="model">fr {m}</span>
                  <span class="who">{who}</span>
                </div>
                <div class="why">{why}</div>
              </div>
            </div>'''
        for m, who, why in cards
    )
    body = f"""
<div class="post post--light">
  <div class="post__content fr-root">
    <span class="kicker"><span class="dot"></span>PERFIL DE USO · PRA QUEM É CADA UM</span>
    <h2 class="fr-cmp-headline">o <span class="hl">match</span> certo.</h2>
    <div class="fr-uc-grid">{cards_html}</div>
    {_foot("04 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_05_cta(img970: Path) -> str:
    body = f"""
<div class="post post--light">
  <div class="post__content fr-root">
    <span class="kicker"><span class="dot"></span>VEREDICTO MERGE</span>
    <div class="fr-cta-grid">
      <div class="fr-cta-left">
        <h2 class="fr-cta-headline">amoled <span class="hl">pra cada</span> bolso.</h2>
        <div class="fr-cta-band">
          <div class="fr-cta-row">
            <span class="lbl">faixa de preço</span>
            <span class="val">r$ 2.499 → 8.999</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">entry-level</span>
            <span class="val">fr 165</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">custo-benefício</span>
            <span class="val">fr 265</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">topo de linha</span>
            <span class="val">fr 970</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">multi-band gps</span>
            <span class="val">265 · 570 · 970</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">ecg + speaker</span>
            <span class="val">570 · 970</span>
          </div>
          <div class="fr-cta-row">
            <span class="lbl">lançamento</span>
            <span class="val">2023 → 2025</span>
          </div>
        </div>
        <p class="fr-cta-where">
          Disponíveis no site Garmin BR, Centauro, Decathlon e revendas autorizadas.
          Versão 165 Music (+ r$ 500) · modelos S em caixa 42mm no 265 e 570.
        </p>
      </div>
      <div class="fr-cta-right">
        <span class="badge">flagship</span>
        <div class="stage"><img src="file://{img970}" alt="fr 970"></div>
        <div class="caption">
          <span>fr 970 · sapphire</span>
          <span class="big">r$ 8.999</span>
        </div>
      </div>
    </div>
    {_foot("05 / 05")}
  </div>
</div>"""
    return _shell(body)


# ---------- RENDER ----------

def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)

    # 1) prep imagens — strip white bg
    print("→ preparando imagens (strip white bg)...")
    imgs: dict[str, Path] = {}
    for m in ["165", "265", "570", "970"]:
        src = BRAND_DIR / f"fr_{m}.jpg"
        if not src.exists():
            raise FileNotFoundError(f"falta press shot: {src}")
        dest = TMP / f"fr_{m}_t.png"
        strip_white_bg(src, dest)
        imgs[m] = dest
        print(f"  ✓ fr {m}")

    # 2) build slides
    slides = [
        ("01_cover",   slide_01_cover(imgs["970"])),
        ("02_lineup",  slide_02_lineup(imgs)),
        ("03_compare", slide_03_compare(imgs)),
        ("04_usecase", slide_04_usecase(imgs)),
        ("05_cta",     slide_05_cta(imgs["970"])),
    ]

    print(f"→ render {SLUG} ({len(slides)} slides)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for slug, html in slides:
            html_file = TMP / f"{slug}.html"
            html_file.write_text(html, encoding="utf-8")
            page.goto(f"file://{html_file}")
            page.wait_for_load_state("networkidle", timeout=15000)
            png = OUT / f"{slug}.png"
            page.screenshot(
                path=str(png), full_page=False, omit_background=False,
                clip={"x": 0, "y": 0, "width": W, "height": H},
            )
            print(f"  ✓ {png.name}")
        browser.close()


if __name__ == "__main__":
    main()
