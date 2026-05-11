"""Carrossel dark-theme · Maratona Rio 26 · Adizero Adios Pro 4.

Edição da coleção Maratona Rio 2026 — refaze do post anterior com voz
técnica (propriedade sobre o gadget), clickbait curto e Maratona logo
em destaque. 4 slides 1080×1350 (Instagram 4:5).

Slides:
  01 cover-evento  · MARATONA DO RIO logo · co-brand MERGE × ADIDAS
  02 flagship      · hero Adios Pro 4 + 5 spec cards técnicos
  03 comparativo   · Pro 3 → Pro 4 (peso, placa, cabedal, drop)
  04 drop oficial  · preço + canais (foto da sola)

Output: output/feed/maratona_rio_2026/<slug>.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "brand"
OUT = ROOT / "output" / "feed" / "maratona_rio_2026"
TMP = ROOT / ".tmp_posts" / "maratona_rio_2026"

W, H = 1080, 1350

LOGO_MARATONA = BRAND / "MaratonaRioLogo_white.png"
HERO_PRO4 = BRAND / "AdiosProSemFundo.png"
SOLA_PRO4 = BRAND / "AdiosProSolaSemFundo.png"


# ---------- shared CSS (dark theme tokens + components) ----------

DARK_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
  --bg:        #0A0A0A;
  --ink:       #F2F2F2;
  --ink-soft:  #C9C9C9;
  --ink-dim:   #7A7A7A;
  --orange:    #FF6900;
  --orange-deep: #E55D00;
  --purple:    #2A1338;
  --hairline:  rgba(255,255,255,0.08);
  --card-bg:   rgba(255,255,255,0.04);
  --card-bd:   rgba(255,255,255,0.10);
}

* { box-sizing: border-box; }
html, body { margin:0; padding:0; }
body {
  background:#222;
  font-family:'Inter', sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
  font-feature-settings:"ss01","cv11";
}

.post {
  position:relative; width:1080px; height:1350px;
  overflow:hidden; margin:0 auto;
  background:
    radial-gradient(80% 60% at 80% 90%, rgba(255,105,0,0.28) 0%, rgba(255,105,0,0) 60%),
    radial-gradient(60% 50% at 20% 10%, rgba(120,60,180,0.35) 0%, rgba(120,60,180,0) 65%),
    linear-gradient(180deg, #1B0E2A 0%, #0A0A0A 55%, #0A0606 100%);
  color: var(--ink);
}
.grid-overlay {
  position:absolute; inset:0; z-index:1; pointer-events:none;
  background-image:
    linear-gradient(rgba(255,255,255,0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.04) 1px, transparent 1px);
  background-size: 90px 90px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.6), rgba(0,0,0,0.2));
}

.content {
  position:relative; z-index:2;
  padding: 70px 70px 80px;
  height:100%;
  display:flex; flex-direction:column;
}

/* ====== KICKER (pill) ====== */
.kicker {
  display:inline-flex; align-items:center; gap:10px;
  padding: 10px 18px; border-radius: 999px;
  background: rgba(255,255,255,0.06);
  border: 1px solid var(--card-bd);
  font-family:'JetBrains Mono', monospace;
  font-size: 15px; font-weight:600;
  letter-spacing: 0.18em; text-transform: uppercase;
  color: var(--ink); width: fit-content;
}
.kicker .dot {
  width:8px; height:8px; border-radius:50%; background: var(--orange);
  box-shadow: 0 0 12px rgba(255,105,0,0.6);
}

.meta-top {
  position:absolute; top:70px; right:70px; z-index:3;
  font-family:'JetBrains Mono', monospace;
  font-size: 14px; letter-spacing: 0.22em; text-transform: uppercase;
  color: var(--ink-soft); text-align: right; line-height: 1.6;
}

/* ====== HEADLINE ====== */
.headline {
  font-family:'Inter', sans-serif; font-weight: 900;
  letter-spacing: -0.04em; line-height: 0.92;
  text-transform: lowercase;
  color: var(--ink);
  margin: 0;
}
.headline .hl { color: var(--orange); }

.lead {
  font-family:'JetBrains Mono', monospace; font-weight: 500;
  font-size: 18px; line-height: 1.5;
  color: var(--ink-soft);
  margin: 0;
  max-width: 880px;
}

/* ====== FOOTER ====== */
.footer {
  margin-top:auto;
  display:flex; align-items:center; justify-content:space-between;
  padding-top: 28px;
  border-top: 1px solid var(--hairline);
}
.brand-mark {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size: 36px; letter-spacing: -0.025em;
  text-transform: lowercase; color: var(--ink);
}
.brand-mark .dot { color: var(--orange); }

.page-no {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 13px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-dim);
}
.right-foot {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 13px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-soft);
}
.right-foot .accent { color: var(--orange); }

/* ====== CO-BRAND PILL ====== */
.cobrand {
  display:inline-flex; align-items:center; gap:14px;
  padding: 11px 22px; border-radius: 999px;
  background: rgba(255,255,255,0.06);
  border: 1px solid var(--card-bd);
  font-family:'Inter', sans-serif; font-weight: 700;
  font-size: 16px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--ink); width: fit-content;
}
.cobrand .dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--orange); box-shadow: 0 0 10px rgba(255,105,0,0.6);
}
.cobrand .x { opacity: 0.5; }
"""


# ---------- per-slide CSS + HTML ----------

def _shell(extra_css: str, body: str) -> str:
    return f"""<!doctype html>
<html><head>
<meta charset="utf-8">
<style>{DARK_CSS}
{extra_css}
</style>
</head><body>{body}</body></html>"""


# Slide 01 — Cover Evento
COVER_CSS = """
.cover-logo {
  display:flex; justify-content:center; margin-top: 18px;
}
.cover-logo img {
  width: 760px; max-width: 86%; height: auto;
  filter: drop-shadow(0 8px 32px rgba(0,0,0,0.4));
}
.cover-mid {
  margin-top: 38px; display:flex; flex-direction:column; align-items:center; gap: 32px;
  text-align: center;
}
.cover-headline {
  font-size: 110px; text-align:center;
}
.cover-stats {
  margin-top: auto; display:grid; grid-template-columns: 1fr 1fr 1fr;
  gap: 18px; padding: 28px 0 6px;
  border-top: 1px solid var(--hairline);
}
.stat {
  display:flex; flex-direction:column; gap: 6px;
}
.stat .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 12px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-dim);
}
.stat .val {
  font-family:'Inter', sans-serif; font-weight: 900;
  font-size: 30px; letter-spacing: -0.03em; line-height: 1.0;
  text-transform: lowercase; color: var(--ink);
}
.stat .val .hl { color: var(--orange); }
"""

def slide_cover() -> str:
    body = f"""
<div class="post">
  <div class="grid-overlay"></div>
  <div class="meta-top">SS26 / DROP 01<br>EDIÇÃO MARATONA RIO 26</div>
  <div class="content">
    <span class="kicker"><span class="dot"></span>DROP OFICIAL · ADIDAS</span>

    <div class="cover-logo">
      <img src="file://{LOGO_MARATONA}" alt="Maratona do Rio">
    </div>

    <div class="cover-mid">
      <span class="cobrand"><span class="dot"></span>MERGE<span class="x">×</span>ADIDAS</span>
      <h1 class="headline cover-headline">
        passada <span class="hl">nova.</span><br>tênis <span class="hl">pronto.</span>
      </h1>
      <p class="lead">a 4ª geração da plataforma que quebrou o recorde mundial feminino — agora na coleção rio 26.</p>
    </div>

    <div class="cover-stats">
      <div class="stat"><span class="lbl">FLAGSHIP</span><span class="val">adios pro <span class="hl">4</span></span></div>
      <div class="stat"><span class="lbl">PESO · US 9</span><span class="val"><span class="hl">138g</span></span></div>
      <div class="stat"><span class="lbl">SOLA</span><span class="val">continental<span class="hl">™</span></span></div>
    </div>

    <div class="footer">
      <span class="brand-mark">merge<span class="dot">.</span></span>
      <span class="right-foot">adidas.com.br · <span class="accent">drop oficial</span></span>
    </div>
  </div>
</div>"""
    return _shell(COVER_CSS, body)


# Slide 02 — Flagship Adios Pro 4
FLAGSHIP_CSS = """
.fl-top {
  display:flex; justify-content:space-between; align-items:flex-start;
  margin-bottom: 22px;
}
.fl-headline {
  font-size: 98px;
  margin-top: 22px;
}
.fl-sub {
  font-family:'JetBrains Mono', monospace; font-weight: 600;
  font-size: 15px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--orange); margin-top: 12px;
}
.fl-body {
  flex: 1; display:flex; flex-direction:row; gap: 28px;
  margin-top: 30px; margin-bottom: 26px; min-height: 0;
  align-items: stretch;
}
.fl-photo {
  flex: 0 0 46%;
  display:flex; align-items:center; justify-content:center;
  position: relative;
  border: 1px solid var(--card-bd);
  border-radius: 14px;
  background:
    radial-gradient(60% 60% at 50% 50%, rgba(255,105,0,0.18) 0%, rgba(255,105,0,0) 70%),
    rgba(255,255,255,0.02);
  overflow:hidden;
}
.fl-photo img { width: 96%; height: auto; object-fit: contain; }
.fl-photo .tag {
  position:absolute; top:14px; left:14px;
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 11px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-soft);
  background: rgba(0,0,0,0.5); padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--card-bd);
}
.fl-specs {
  flex: 1; display:flex; flex-direction:column; gap: 12px;
}
.spec-card {
  flex: 1;
  display:flex; flex-direction:column; justify-content:center;
  padding: 16px 20px;
  border: 1px solid var(--card-bd);
  border-radius: 12px;
  background: var(--card-bg);
}
.spec-card .k {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 11px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-dim);
}
.spec-card .v {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size: 30px; letter-spacing: -0.025em; line-height: 1.0;
  text-transform: lowercase; color: var(--ink);
  margin-top: 4px;
}
.spec-card .v .hl { color: var(--orange); }
.spec-card .d {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size: 13px; line-height: 1.4; color: var(--ink-soft);
  margin-top: 6px;
}
"""

def slide_flagship() -> str:
    body = f"""
<div class="post">
  <div class="grid-overlay"></div>
  <div class="meta-top">02 / 04<br>FLAGSHIP · TÊNIS</div>
  <div class="content">
    <span class="kicker"><span class="dot"></span>FLAGSHIP · ADIZERO ADIOS PRO 4</span>

    <h1 class="headline fl-headline">
      <span class="hl">138g</span>.<br>menos que um iphone.
    </h1>
    <div class="fl-sub">A 4ª GERAÇÃO DA PLATAFORMA DO WR FEMININO DA MARATONA.</div>

    <div class="fl-body">
      <div class="fl-photo">
        <span class="tag">VISTA LATERAL</span>
        <img src="file://{HERO_PRO4}" alt="Adios Pro 4">
      </div>
      <div class="fl-specs">
        <div class="spec-card">
          <div class="k">PESO · US 9</div>
          <div class="v"><span class="hl">138g</span></div>
          <div class="d">–21g vs pro 3 · um dos racing mais leves do mercado.</div>
        </div>
        <div class="spec-card">
          <div class="k">GEOMETRIA</div>
          <div class="v">drop <span class="hl">6.5mm</span> · stack 39/32.5</div>
          <div class="d">rocker pronunciado · transição forçada no antepé.</div>
        </div>
        <div class="spec-card">
          <div class="k">PROPULSÃO</div>
          <div class="v">energyrods <span class="hl">2.0</span></div>
          <div class="d">5 hastes de carbono · curva otimizada por modal racing.</div>
        </div>
        <div class="spec-card">
          <div class="k">AMORTECIMENTO · CABEDAL</div>
          <div class="v">lightstrike pro · <span class="hl">lightlock</span></div>
          <div class="d">espuma top-tier (igual takumi sen) + mesh ripstop.</div>
        </div>
      </div>
    </div>

    <div class="footer">
      <span class="brand-mark">merge<span class="dot">.</span></span>
      <span class="right-foot">R$ <span class="accent">2.499</span> · adidas.com.br</span>
    </div>
  </div>
</div>"""
    return _shell(FLAGSHIP_CSS, body)


# Slide 03 — Comparativo Pro 3 → Pro 4
COMPARE_CSS = """
.cmp-headline { font-size: 90px; margin-top: 22px; }
.cmp-sub {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size: 18px; line-height: 1.45; color: var(--ink-soft);
  margin-top: 16px; max-width: 720px;
}
.cmp-table {
  flex:1; display:flex; flex-direction:column;
  margin-top: 36px; margin-bottom: 24px;
  border: 1px solid var(--card-bd); border-radius: 14px;
  background: var(--card-bg);
  overflow: hidden;
}
.cmp-head, .cmp-row {
  display:grid; grid-template-columns: 1.1fr 1fr 1fr;
  align-items:center;
  padding: 18px 24px;
}
.cmp-head {
  background: rgba(255,255,255,0.04);
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size: 12px; letter-spacing: 0.22em; text-transform: uppercase;
  color: var(--ink-dim);
}
.cmp-head .right { color: var(--orange); }
.cmp-row {
  flex: 1;
  border-top: 1px solid var(--hairline);
}
.cmp-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 13px; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--ink-dim);
}
.cmp-row .v {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size: 26px; letter-spacing: -0.02em; line-height: 1.1;
  text-transform: lowercase; color: var(--ink-soft);
}
.cmp-row .v.right { color: var(--ink); }
.cmp-row .v.right .hl { color: var(--orange); }
"""

def slide_compare() -> str:
    rows = [
        ("peso (us 9)",      "159 g",            '<span class="hl">138 g</span>'),
        ("placa",            "energyrods 1.0",   '<span class="hl">energyrods 2.0</span>'),
        ("cabedal",          "celermesh 3",      '<span class="hl">lightlock ripstop</span>'),
        ("drop",             "6 mm",             '<span class="hl">6.5 mm</span>'),
        ("stack ant/post",   "39 / 33 mm",       '<span class="hl">39 / 32.5 mm</span>'),
        ("sola",             "continental™",     'continental™'),
    ]
    rows_html = "\n".join(
        f'<div class="cmp-row"><span class="lbl">{lbl}</span>'
        f'<span class="v">{left}</span>'
        f'<span class="v right">{right}</span></div>'
        for lbl, left, right in rows
    )
    body = f"""
<div class="post">
  <div class="grid-overlay"></div>
  <div class="meta-top">03 / 04<br>EVOLUÇÃO TÉCNICA</div>
  <div class="content">
    <span class="kicker"><span class="dot"></span>PRO 3 → PRO 4</span>

    <h1 class="headline cmp-headline">o que <span class="hl">mudou</span><br>de fato.</h1>
    <p class="cmp-sub">peso, placa e cabedal — três frentes onde a quarta geração ganha em relação ao tênis que já era WR.</p>

    <div class="cmp-table">
      <div class="cmp-head">
        <span></span>
        <span>ADIOS PRO 3</span>
        <span class="right">ADIOS PRO 4</span>
      </div>
      {rows_html}
    </div>

    <div class="footer">
      <span class="brand-mark">merge<span class="dot">.</span></span>
      <span class="page-no">03 / 04 · COMPARATIVO</span>
    </div>
  </div>
</div>"""
    return _shell(COMPARE_CSS, body)


# Slide 04 — Drop oficial / onde comprar
DROP_CSS = """
.drop-top { display:flex; justify-content:space-between; align-items:flex-start; }
.drop-headline { font-size: 96px; margin-top: 22px; }
.drop-sub {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size: 18px; line-height: 1.45; color: var(--ink-soft);
  margin-top: 14px; max-width: 760px;
}
.drop-body {
  flex:1; display:flex; flex-direction:row; gap: 30px;
  margin-top: 34px; margin-bottom: 26px; min-height: 0;
  align-items: stretch;
}
.drop-photo {
  flex: 0 0 38%;
  display:flex; align-items:center; justify-content:center;
  border: 1px solid var(--card-bd); border-radius: 14px;
  background:
    radial-gradient(70% 70% at 50% 50%, rgba(255,105,0,0.20) 0%, rgba(255,105,0,0) 70%),
    rgba(255,255,255,0.02);
  position: relative; overflow:hidden;
}
.drop-photo img { width: 80%; height: auto; object-fit: contain; }
.drop-photo .tag {
  position:absolute; top:14px; left:14px;
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 11px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-soft);
  background: rgba(0,0,0,0.5); padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--card-bd);
}
.drop-channels { flex:1; display:flex; flex-direction:column; gap: 14px; }
.channel {
  flex:1; padding: 18px 22px;
  border: 1px solid var(--card-bd); border-radius: 12px;
  background: var(--card-bg);
  display:flex; flex-direction:column; justify-content:center;
}
.channel .k {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 11px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-dim);
}
.channel .v {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size: 28px; letter-spacing: -0.02em; line-height: 1.0;
  text-transform: lowercase; color: var(--ink); margin-top: 6px;
}
.channel .v .hl { color: var(--orange); }
.channel .d {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size: 13px; line-height: 1.4; color: var(--ink-soft); margin-top: 6px;
}

.price-bar {
  display:flex; justify-content:space-between; align-items:baseline;
  padding: 18px 24px; margin-bottom: 18px;
  border: 1px solid var(--card-bd); border-radius: 12px;
  background: rgba(255,255,255,0.02);
}
.price-bar .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size: 12px; letter-spacing: 0.20em; text-transform: uppercase;
  color: var(--ink-dim);
}
.price-bar .val {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size: 56px; letter-spacing: -0.035em;
  color: var(--ink);
}
.price-bar .val .hl { color: var(--orange); }
.price-bar .val .cents { font-size: 28px; vertical-align: super; opacity: 0.75; margin-left: 2px; }
"""

def slide_drop() -> str:
    body = f"""
<div class="post">
  <div class="grid-overlay"></div>
  <div class="meta-top">04 / 04<br>DROP OFICIAL · BRASIL</div>
  <div class="content">
    <span class="kicker"><span class="dot"></span>DISPONIBILIDADE · BRASIL</span>

    <h1 class="headline drop-headline">vai correr <span class="hl">quanto</span>?</h1>
    <p class="drop-sub">edição maratona rio 26 disponível no drop oficial da adidas e lojas autorizadas.</p>

    <div class="drop-body">
      <div class="drop-photo">
        <span class="tag">SOLA · CONTINENTAL</span>
        <img src="file://{SOLA_PRO4}" alt="Adios Pro 4 sola">
      </div>
      <div class="drop-channels">
        <div class="price-bar">
          <span class="lbl">A PARTIR DE</span>
          <span class="val">R$ <span class="hl">2.499</span><span class="cents">,99</span></span>
        </div>
        <div class="channel">
          <div class="k">DROP OFICIAL</div>
          <div class="v">adidas<span class="hl">.com.br</span></div>
          <div class="d">retire em 24h · pagamento em até 10× sem juros.</div>
        </div>
        <div class="channel">
          <div class="k">VAREJO ESPECIALIZADO</div>
          <div class="v">centauro · <span class="hl">netshoes</span></div>
          <div class="d">estoque limitado · cores edição maratona rio.</div>
        </div>
        <div class="channel">
          <div class="k">FLAGSHIP · RJ</div>
          <div class="v">adidas store <span class="hl">rio</span></div>
          <div class="d">prova presencial · botafogo &amp; shopping leblon.</div>
        </div>
      </div>
    </div>

    <div class="footer">
      <span class="brand-mark">merge<span class="dot">.</span></span>
      <span class="right-foot">drop ativo · <span class="accent">25.05.26</span></span>
    </div>
  </div>
</div>"""
    return _shell(DROP_CSS, body)


# ---------- runner ----------

SLIDES = [
    ("01_cover",     slide_cover),
    ("02_flagship",  slide_flagship),
    ("03_compare",   slide_compare),
    ("04_drop",      slide_drop),
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)

    print(f"→ maratona_rio_2026 ({len(SLIDES)} slides)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for slug, builder in SLIDES:
            html = builder()
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
    print(f"\nDONE → {OUT}")


if __name__ == "__main__":
    main()
