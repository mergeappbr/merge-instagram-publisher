"""Carrossel review: Theragun PRO Plus (Therabody) — deep dive single product.

5 slides 1080×1350 cream, design system wr-*. Single hero (não comparativo)
— foco nas 6 terapias do Pro Plus. Press shot oficial Therabody (fundo
cream, LED vermelho aceso). Importação BR.

Output: output/posts/theragun_pro_plus/01_cover.png ... 05_cta.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_STYLES = (ROOT / "templates" / "posts" / "_post_styles.css").read_text(encoding="utf-8")

W, H = 1080, 1350
SLUG = "theragun_pro_plus"
OUT = ROOT / "output" / "posts" / SLUG
TMP = ROOT / ".tmp_posts" / SLUG

HERO_IMG = ROOT / "brand" / "therabody" / "pro_plus.png"

WHITE_THRESHOLD = 244
TOLERANCE = 18


def strip_cream_bg(src: Path, dest: Path) -> Path:
    """Strip cream/off-white bg → transparente. Foto Therabody tem fundo
    creme uniforme. Mantém o LED vermelho e o dial azul intactos."""
    img = Image.open(src).convert("RGBA")
    px = img.load()
    w, h = img.size
    corners = [px[2, 2], px[w-3, 2], px[2, h-3], px[w-3, h-3],
               px[w//2, 2], px[w//2, h-3]]
    avg = (sum(c[0] for c in corners)//len(corners),
           sum(c[1] for c in corners)//len(corners),
           sum(c[2] for c in corners)//len(corners))
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0: continue
            # bg cream: claro e perto do avg dos cantos
            if (abs(r-avg[0]) <= TOLERANCE and abs(g-avg[1]) <= TOLERANCE
                    and abs(b-avg[2]) <= TOLERANCE and r >= 220 and g >= 215 and b >= 200):
                px[x, y] = (r, g, b, 0)
    bbox = img.getbbox()
    if bbox: img = img.crop(bbox)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


# 6 terapias do Pro Plus
TERAPIAS = [
    ("percussão",  "até 2.400 ppm · amplitude 16 mm · motor brushless quietforce"),
    ("calor",      "3 níveis · 45 / 50 / 55 °C · aquece tecido antes do trabalho profundo"),
    ("frio",       "3 níveis · 5 / 10 / 15 °C · attachment vendido à parte (us$ 99)"),
    ("vibração",   "3 frequências · 47 / 50 / 53 hz · terapia de baixa intensidade"),
    ("led vermelho","near-infrared no attachment · circulação + recovery localizado"),
    ("breathwork", "guia de respiração no app therabody · downregulation pós-treino"),
]

# specs técnicas
SPECS = [
    ("ppm máx",      "2.400 percussões/min"),
    ("amplitude",    "16 mm (deep tissue)"),
    ("calor",        "45 · 50 · 55 °C"),
    ("frio",         "5 · 10 · 15 °C (add-on)"),
    ("bateria",      "~150 min · troca rápida"),
    ("sensor",       "fc biométrico embutido"),
    ("attachments",  "5 + calor + vibração"),
    ("preço (br)",   "≈ r$ 4.500 (importação)"),
]


CSS = """
.wr-root { display:flex; flex-direction:column; height:100%; padding-bottom: 24px; }

.wr-foot { margin-top:auto; padding-top:18px; display:flex; align-items:center; justify-content:space-between; }
.wr-foot .mark { font-family:'Inter', sans-serif; font-weight:900; font-size:42px; letter-spacing:-0.025em; text-transform:lowercase; color: var(--ink-dark); }
.wr-foot .mark .dot { color: var(--orange); }
.wr-foot .page { font-family:'JetBrains Mono', monospace; font-weight:600; font-size:17px; letter-spacing:0.18em; text-transform:uppercase; color: var(--ink-dark); opacity:0.55; }

.post .kicker { font-family:'JetBrains Mono', monospace; font-weight:700; font-size:18px !important; letter-spacing:0.18em; text-transform:uppercase; color: var(--ink-dark); opacity:0.78; }

/* cover */
.wr-cover-head { display:flex; flex-direction:column; gap:0; }
.wr-cover-title { font-family:'Inter', sans-serif; font-weight:900; font-size:118px; line-height:0.86; letter-spacing:-0.055em; margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark); }
.wr-cover-title .hl { color: var(--orange) !important; }
.wr-cover-cap { font-family:'JetBrains Mono', monospace; font-weight:600; font-size:18px; letter-spacing:0.16em; text-transform:uppercase; color: var(--ink-dark); opacity:0.78; margin-top:18px; }
.wr-cover-sub { font-family:'Inter', sans-serif; font-weight:800; font-size:46px; line-height:1.05; letter-spacing:-0.025em; margin:16px 0 0; text-transform:lowercase; color: var(--ink-dark); }
.wr-cover-sub .hl { color: var(--orange) !important; }
.wr-cover-lead { font-family:'JetBrains Mono', monospace; font-weight:500; font-size:22px; line-height:1.5; color: var(--ink-dark); margin:18px 0 0; max-width: 920px; }

.wr-cover-hero-single { flex:1; display:flex; align-items:center; justify-content:center; margin-top:10px; min-height:0; }
.wr-cover-hero-single img { max-width:88%; max-height:100%; object-fit:contain; }

.wr-cover-priceband { margin-top:18px; padding-top:18px; border-top: 2px solid rgba(10,10,10,0.14); display:flex; align-items:baseline; justify-content:space-between; }
.wr-cover-priceband .label { font-family:'JetBrains Mono', monospace; font-weight:700; font-size:14px; letter-spacing:0.16em; text-transform:uppercase; color: var(--ink-dark); opacity:0.7; }
.wr-cover-priceband .price { font-family:'Inter', sans-serif; font-weight:900; font-size:52px; letter-spacing:-0.03em; color: var(--orange); line-height:1; }

/* headline */
.wr-headline { font-family:'Inter', sans-serif; font-weight:900; font-size:64px; line-height:1.0; letter-spacing:-0.035em; margin:14px 0 22px; text-transform:lowercase; color: var(--ink-dark); }
.wr-headline .hl { color: var(--orange) !important; }

/* lineup → 6 terapias (2 col grid) */
.wr-tlist { flex:1; display:grid; grid-template-columns: 1fr 1fr; gap:16px; margin-top:6px; margin-bottom:14px; min-height:0; }
.wr-tcard { display:flex; flex-direction:column; justify-content:center; gap:8px; padding:20px 24px; background: rgba(10,10,10,0.035); border-left: 5px solid var(--orange); min-height:0; }
.wr-tcard .n { font-family:'JetBrains Mono', monospace; font-weight:700; font-size:13px; letter-spacing:0.16em; color: var(--orange); }
.wr-tcard .t { font-family:'Inter', sans-serif; font-weight:900; font-size:30px; letter-spacing:-0.03em; color: var(--ink-dark); line-height:1; text-transform:lowercase; }
.wr-tcard .d { font-family:'JetBrains Mono', monospace; font-weight:500; font-size:13px; line-height:1.45; color: var(--ink-dark-soft); }

/* compare → spec sheet single column, com hero ao lado */
.wr-spec-grid { flex:1; display:grid; grid-template-columns: 1fr 0.85fr; gap:30px; min-height:0; }
.wr-spec-table { display:flex; flex-direction:column; min-height:0; }
.wr-spec-row { flex:1; display:flex; align-items:center; justify-content:space-between; padding:12px 0; border-bottom: 1px solid rgba(10,10,10,0.10); }
.wr-spec-row .lbl { font-family:'JetBrains Mono', monospace; font-weight:700; font-size:13px; letter-spacing:0.14em; text-transform:uppercase; color: var(--ink-dark); opacity:0.65; }
.wr-spec-row .val { font-family:'Inter', sans-serif; font-weight:800; font-size:18px; letter-spacing:-0.015em; color: var(--ink-dark); text-align:right; }
.wr-spec-row .val.is-hl { color: var(--orange); font-weight:900; }
.wr-spec-hero { display:flex; align-items:center; justify-content:center; background: rgba(10,10,10,0.035); border:1.5px solid rgba(10,10,10,0.10); padding:14px; }
.wr-spec-hero img { max-width:100%; max-height:100%; object-fit:contain; }

/* use-case */
.wr-uc-grid { flex:1; display:flex; flex-direction:column; gap:18px; margin-top:10px; margin-bottom:10px; min-height:0; }
.wr-uc-card { flex:1; display:flex; flex-direction:column; justify-content:center; gap:8px; padding:22px 30px; background: rgba(10,10,10,0.035); border-left: 5px solid var(--orange); min-height:0; }
.wr-uc-card .who { font-family:'Inter', sans-serif; font-weight:900; font-size:30px; letter-spacing:-0.025em; color: var(--orange); line-height:1; text-transform:lowercase; }
.wr-uc-card .why { font-family:'JetBrains Mono', monospace; font-weight:500; font-size:16px; line-height:1.5; color: var(--ink-dark-soft); }

/* cta */
.wr-cta-grid { flex:1; display:grid; grid-template-columns: 0.9fr 1.1fr; gap:30px; margin-top:18px; min-height:0; }
.wr-cta-left { display:flex; flex-direction:column; }
.wr-cta-headline { font-family:'Inter', sans-serif; font-weight:900; font-size:70px; line-height:0.94; letter-spacing:-0.045em; margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark); }
.wr-cta-headline .hl { color: var(--orange) !important; }
.wr-cta-band { flex:1; margin-top:26px; display:flex; flex-direction:column; justify-content: space-between; gap:8px; }
.wr-cta-row { display:flex; justify-content:space-between; align-items:baseline; padding-bottom:14px; border-bottom: 1px solid rgba(10,10,10,0.12); }
.wr-cta-row .lbl { font-family:'JetBrains Mono', monospace; font-weight:600; font-size:13px; letter-spacing:0.16em; text-transform:uppercase; color: var(--ink-dark); opacity:0.7; }
.wr-cta-row .val { font-family:'Inter', sans-serif; font-weight:900; font-size:20px; letter-spacing:-0.025em; color: var(--orange); text-transform:lowercase; }
.wr-cta-where { font-family:'JetBrains Mono', monospace; font-weight:500; font-size:14px; line-height:1.55; color: var(--ink-dark); opacity:0.78; margin-top:18px; }
.wr-cta-right { display:flex; flex-direction:column; background: rgba(10,10,10,0.04); border: 1.5px solid rgba(10,10,10,0.10); position:relative; overflow:hidden; }
.wr-cta-right .badge { position:absolute; top:14px; left:14px; z-index:2; font-family:'JetBrains Mono', monospace; font-weight:700; font-size:12px; letter-spacing:0.18em; text-transform:uppercase; padding:8px 12px; background:var(--orange); color:var(--ink-dark); }
.wr-cta-right .stage { flex:1; display:flex; align-items:center; justify-content:center; padding:18px; min-height:0; }
.wr-cta-right .stage img { max-width:100%; max-height:100%; object-fit:contain; }
.wr-cta-right .caption { padding:16px 20px; border-top:1.5px solid rgba(10,10,10,0.10); font-family:'JetBrains Mono', monospace; font-weight:600; font-size:14px; letter-spacing:0.04em; color: var(--ink-dark); display:flex; justify-content:space-between; align-items:baseline; }
.wr-cta-right .caption .big { font-family:'Inter', sans-serif; font-weight:900; font-size:22px; letter-spacing:-0.02em; color: var(--orange); }
"""


def _shell(body: str) -> str:
    return ('<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><style>'
            f"{BASE_STYLES}{CSS}</style></head><body>{body}</body></html>")


def _foot(page: str) -> str:
    return ('<div class="wr-foot"><span class="mark">merge<span class="dot">.</span></span>'
            f'<span class="page">{page}</span></div>')


def slide_01_cover(hero: Path) -> str:
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>THERABODY · RECOVERY · 2026</span>
    <div class="wr-cover-head">
      <h1 class="wr-cover-title">6 terapias.<br>1 <span class="hl">aparelho</span>.</h1>
      <span class="wr-cover-cap">theragun pro plus · gen 6 · importação br</span>
      <h2 class="wr-cover-sub">recovery virou <span class="hl">multi-terapia</span>.</h2>
      <p class="wr-cover-lead">
        Percussão profunda, calor, frio, vibração, LED vermelho e breathwork
        no mesmo corpo. A Therabody empilhou 6 ciências de recuperação num
        massageador só. Vale os R$ 4.500 de importação?
      </p>
    </div>
    <div class="wr-cover-hero-single">
      <img src="file://{hero}" alt="theragun pro plus">
    </div>
    <div class="wr-cover-priceband">
      <span class="label">theragun pro plus · gen 6</span>
      <span class="price">≈ r$ 4.500</span>
    </div>
    {_foot("01 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_02_terapias() -> str:
    cards = ""
    for i, (t, d) in enumerate(TERAPIAS, start=1):
        cards += f'''<div class="wr-tcard">
              <span class="n">terapia {i:02d}</span>
              <span class="t">{t}</span>
              <span class="d">{d}</span>
            </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>O QUE TEM DENTRO · 6 CIÊNCIAS</span>
    <h2 class="wr-headline">seis terapias, <span class="hl">um corpo</span>.</h2>
    <div class="wr-tlist">{cards}</div>
    {_foot("02 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_03_specs(hero: Path) -> str:
    rows = ""
    hl_labels = {"ppm máx", "amplitude", "preço (br)"}
    for lbl, val in SPECS:
        cls = " is-hl" if lbl in hl_labels else ""
        rows += f'<div class="wr-spec-row"><span class="lbl">{lbl}</span><span class="val{cls}">{val}</span></div>'
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>SPEC SHEET · FICHA TÉCNICA</span>
    <h2 class="wr-headline">os <span class="hl">números</span> que importam.</h2>
    <div class="wr-spec-grid">
      <div class="wr-spec-table">{rows}</div>
      <div class="wr-spec-hero"><img src="file://{hero}" alt="theragun pro plus"></div>
    </div>
    {_foot("03 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_04_usecase() -> str:
    cards = [
        ("maratonista / triatleta",
         "calor pré-treino pra aquecer panturrilha e quadríceps · percussão 16 mm pós-longão pra acelerar limpeza metabólica · frio nos dias de pico pra controlar inflamação."),
        ("força e crossfit",
         "percussão profunda nos grandes grupos antes do wod · vibração de baixa intensidade pra ativação · led vermelho localizado em tendão ou articulação que reclama."),
        ("quem trata o recovery a sério",
         "fc biométrico + breathwork no app pra baixar o sistema nervoso pós-treino · um aparelho substitui pistola + bolsa térmica + máscara de led. quem soma os gadgets paga mais."),
    ]
    cards_html = ""
    for who, why in cards:
        cards_html += f'''<div class="wr-uc-card">
              <span class="who">{who}</span>
              <span class="why">{why}</span>
            </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>PERFIL DE USO · PRA QUEM VALE</span>
    <h2 class="wr-headline">o <span class="hl">match</span> certo pro seu recovery.</h2>
    <div class="wr-uc-grid">{cards_html}</div>
    {_foot("04 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_05_cta(hero: Path) -> str:
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>VEREDICTO MERGE · BR IMPORTAÇÃO</span>
    <div class="wr-cta-grid">
      <div class="wr-cta-left">
        <h2 class="wr-cta-headline">6 gadgets <span class="hl">num</span> só.</h2>
        <div class="wr-cta-band">
          <div class="wr-cta-row">
            <span class="lbl">preço us</span>
            <span class="val">us$ 599</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">preço br importação</span>
            <span class="val">≈ r$ 4.500</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">percussão</span>
            <span class="val">2.400 ppm · 16 mm</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">calor + frio + led</span>
            <span class="val">só pro plus</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">frio (add-on)</span>
            <span class="val">+ us$ 99</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">vale a pena se</span>
            <span class="val">usa 3+ terapias</span>
          </div>
        </div>
        <p class="wr-cta-where">
          Therabody não tem loja oficial BR — importação direta therabody.com
          (us$ 599) ou revenda nacional / Mercado Livre. Frio é attachment à
          parte. Pra quem usaria só percussão, o Theragun Prime resolve mais barato.
        </p>
      </div>
      <div class="wr-cta-right">
        <span class="badge">6 terapias · gen 6</span>
        <div class="stage"><img src="file://{hero}" alt="theragun pro plus"></div>
        <div class="caption">
          <span>theragun pro plus</span>
          <span class="big">r$ 4.500</span>
        </div>
      </div>
    </div>
    {_foot("05 / 05")}
  </div>
</div>"""
    return _shell(body)


def main() -> None:
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    if TMP.exists(): shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)

    print("→ preparando hero (strip cream bg)...")
    if not HERO_IMG.exists():
        raise FileNotFoundError(f"falta press shot: {HERO_IMG}")
    hero = TMP / "pro_plus.png"
    strip_cream_bg(HERO_IMG, hero)
    print(f"  ✓ hero pronto")

    slides = [
        ("01_cover",   slide_01_cover(hero)),
        ("02_terapias", slide_02_terapias()),
        ("03_specs",   slide_03_specs(hero)),
        ("04_usecase", slide_04_usecase()),
        ("05_cta",     slide_05_cta(hero)),
    ]

    print(f"→ render {SLUG} ({len(slides)} slides)")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=2)
        for slug, html in slides:
            html_file = TMP / f"{slug}.html"
            html_file.write_text(html, encoding="utf-8")
            page.goto(f"file://{html_file}", wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=60000)
            png = OUT / f"{slug}.png"
            page.screenshot(path=str(png), full_page=False, omit_background=False,
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            print(f"  ✓ {png.name}")
        browser.close()


if __name__ == "__main__":
    main()
