"""Carrossel review: Canyon Aeroad — 3 tiers (CF SL · CF SLX · CFR).

5 slides 1080×1350 cream, mesmo design system wr-* dos outros reviews.
Canyon Brasil = mercado secundário (importação cinza). Mesmo frame
aero, 3 níveis de carbono/groupset/wheels. Press shot CFR preto com
DT Swiss carbon — usado nos 3 tiers porque shape é idêntica.

Output: output/posts/canyon_aeroad_cfr/01_cover.png ... 05_cta.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_STYLES = (ROOT / "templates" / "posts" / "_post_styles.css").read_text(encoding="utf-8")

W, H = 1080, 1350
SLUG = "canyon_aeroad_cfr"
OUT = ROOT / "output" / "posts" / SLUG
TMP = ROOT / ".tmp_posts" / SLUG

WHITE_THRESHOLD = 240
TOLERANCE = 22


def strip_bg(src: Path, dest: Path) -> Path:
    """Strip bg (cerca de madeira atrás da bike). Foto é JPG outdoor —
    strip vai pegar pouco. Pra esse review, deixa o fundo de madeira como
    'lifestyle outdoor' (válido pra bike). Apenas crop bbox."""
    img = Image.open(src).convert("RGBA")
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


# 3 tiers da família Aeroad
BIKES = [
    {
        "slug": "cf_sl",
        "short": "aeroad cf sl 8",
        "brand": "canyon",
        "full":  "canyon aeroad cf sl 8",
        "frame": "cf sl carbon · 1.150 g",
        "groupset": "shimano 105 di2 12s",
        "wheels":   "dt swiss arc 1600 disc",
        "weight":   "7,9 kg (size m)",
        "drive":    "12s · 50/34 · 11–34",
        "cockpit":  "canyon cp10 (one-piece)",
        "sub":   "entry aero · 105 di2",
        "price_us": "us$ 4.299 / € 4.299",
        "price":  "≈ r$ 42.000 (importação)",
        "year":  "2026",
        "tier":  "entry race · 2026",
        "img":   ROOT / "brand" / "canyon" / "aeroad_cfr.jpg",
    },
    {
        "slug": "cf_slx",
        "short": "aeroad cf slx 8",
        "brand": "canyon",
        "full":  "canyon aeroad cf slx 8 di2",
        "frame": "cf slx carbon · 1.050 g",
        "groupset": "shimano ultegra di2 12s",
        "wheels":   "dt swiss arc 1400 disc 50mm",
        "weight":   "7,5 kg (size m)",
        "drive":    "12s · 52/36 · 11–30",
        "cockpit":  "canyon cp10 cfr (carbono)",
        "sub":   "sweet spot · ultegra di2",
        "price_us": "us$ 6.299 / € 6.299",
        "price":  "≈ r$ 62.000 (importação)",
        "year":  "2026",
        "tier":  "race · sweet spot · 2026",
        "img":   ROOT / "brand" / "canyon" / "aeroad_cfr.jpg",
    },
    {
        "slug": "cfr",
        "short": "aeroad cfr di2",
        "brand": "canyon",
        "full":  "canyon aeroad cfr di2",
        "frame": "cfr carbon · 960 g (t1100/t800/m40x)",
        "groupset": "shimano dura-ace di2 12s",
        "wheels":   "dt swiss arc 1100 disc 62mm",
        "weight":   "7,0 kg (size m)",
        "drive":    "12s · 52/36 · 11–30 · power meter",
        "cockpit":  "canyon cp0040 cfr (aero cockpit)",
        "sub":   "flagship · dura-ace di2",
        "price_us": "us$ 9.499 / € 9.999",
        "price":  "≈ r$ 88.000 (importação)",
        "year":  "2026",
        "tier":  "flagship · van der poel · 2026",
        "img":   ROOT / "brand" / "canyon" / "aeroad_cfr.jpg",
    },
]


CSS = """
.wr-root { display:flex; flex-direction:column; height:100%; padding-bottom: 24px; }

.wr-foot { margin-top:auto; padding-top:18px; display:flex; align-items:center; justify-content:space-between; }
.wr-foot .mark {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:42px; letter-spacing:-0.025em;
  text-transform:lowercase; color: var(--ink-dark);
}
.wr-foot .mark .dot { color: var(--orange); }
.wr-foot .page {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:17px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.55;
}

.post .kicker {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:18px !important; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.78;
}

.wr-cover-head { display:flex; flex-direction:column; gap:0; }
.wr-cover-title {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:128px; line-height:0.86; letter-spacing:-0.055em;
  margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.wr-cover-title .hl { color: var(--orange) !important; }
.wr-cover-cap {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:18px; letter-spacing:0.16em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.78;
  margin-top:18px;
}
.wr-cover-sub {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size:48px; line-height:1.05; letter-spacing:-0.025em;
  margin:16px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.wr-cover-sub .hl { color: var(--orange) !important; }
.wr-cover-lead {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:22px; line-height:1.5; color: var(--ink-dark);
  margin:18px 0 0; max-width: 920px;
}

/* hero único — single bike side view (full width) */
.wr-cover-hero-single {
  flex:1; display:flex; align-items:center; justify-content:center;
  margin-top:18px; min-height:0;
}
.wr-cover-hero-single img { max-width:100%; max-height:100%; object-fit:contain; }

/* escada de preços (3 cols) */
.wr-cover-ladder {
  margin-top:22px; padding-top:18px;
  display:grid; grid-template-columns: repeat(3, 1fr); gap:22px;
  border-top: 2px solid rgba(10,10,10,0.14);
}
.wr-cover-ladder .col {
  display:flex; flex-direction:column; gap:6px;
  padding-right:18px;
  border-right: 1px solid rgba(10,10,10,0.10);
}
.wr-cover-ladder .col:last-child { border-right:0; padding-right:0; }
.wr-cover-ladder .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:12px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.62;
}
.wr-cover-ladder .mdl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:24px; letter-spacing:-0.022em;
  text-transform:lowercase; color: var(--ink-dark); line-height:1.05;
  min-height: 2.2em;
  display:flex; align-items:flex-start;
}
.wr-cover-ladder .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:26px; letter-spacing:-0.02em; color: var(--orange); line-height:1;
  margin-top:2px;
}
.wr-cover-ladder .tag {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:11px; letter-spacing:0.14em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.62; margin-top:6px;
}

/* lineup / headlines */
.wr-headline {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:64px; line-height:1.0; letter-spacing:-0.035em;
  margin:14px 0 22px; text-transform:lowercase; color: var(--ink-dark);
}
.wr-headline .hl { color: var(--orange) !important; }

.wr-lineup {
  flex:1; display:grid; grid-template-columns: 1fr 1fr 1fr;
  gap:18px; margin-top:6px; margin-bottom:14px; min-height:0;
}
.wr-card {
  display:grid; grid-template-rows: 1fr auto;
  background: rgba(10,10,10,0.035);
  border: 1.5px solid rgba(10,10,10,0.10);
  overflow:hidden; min-height:0;
}
.wr-card-img {
  display:flex; align-items:center; justify-content:center;
  padding:14px; min-height:0;
}
.wr-card-img img { max-width: 100%; max-height: 100%; object-fit: cover; }
.wr-card-body { padding:14px 18px 18px; border-top:1.5px solid rgba(10,10,10,0.10); }
.wr-card-head {
  display:flex; align-items:baseline; justify-content:space-between; gap:10px;
  margin-bottom:6px;
}
.wr-card .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:11px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:24px; line-height:0.96; letter-spacing:-0.028em;
  color: var(--ink-dark); text-transform:lowercase;
}
.wr-card .pitch {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:12px; line-height:1.45; color: var(--ink-dark-soft);
  margin:6px 0 0;
}
.wr-card .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:24px; letter-spacing:-0.025em; color: var(--orange);
  margin-top:8px;
}

/* compare (spec sheet com thumbs) */
.wr-cmp-thumbs {
  display:grid; grid-template-columns: 1.05fr 1fr 1fr 1fr;
  align-items:end; gap:0; padding-bottom:12px;
  border-bottom: 2px solid rgba(10,10,10,0.14);
}
.wr-cmp-thumb { display:flex; flex-direction:column; align-items:center; gap:6px; }
.wr-cmp-thumb img { width: 140px; height: 90px; object-fit: cover; }
.wr-cmp-thumb .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:11px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-cmp-thumb .lbl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:17px; letter-spacing:-0.02em; color: var(--ink-dark);
  text-transform:lowercase; text-align:center; line-height:1.05;
}
.wr-cmp-table {
  flex:1; display:flex; flex-direction:column; min-height:0; margin-top:0;
}
.wr-cmp-row {
  flex:1;
  display:grid; grid-template-columns: 1.05fr 1fr 1fr 1fr;
  align-items:center;
  padding:10px 0;
  border-bottom: 1px solid rgba(10,10,10,0.08);
}
.wr-cmp-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:13px; letter-spacing:0.16em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-cmp-row .val {
  font-family:'Inter', sans-serif; font-weight:700;
  font-size:15px; letter-spacing:-0.01em;
  color: var(--ink-dark); line-height:1.2; text-align:center;
}
.wr-cmp-row .val.is-hl { color: var(--orange); font-weight:900; }

/* use-case */
.wr-uc-grid {
  flex:1; display:flex; flex-direction:column; gap:16px;
  margin-top:10px; margin-bottom:10px; min-height:0;
}
.wr-uc-card {
  flex:1; display:grid; grid-template-columns: 180px 1fr;
  gap:26px; align-items:center;
  padding:18px 26px;
  background: rgba(10,10,10,0.035);
  border-left: 5px solid var(--orange);
  min-height:0;
}
.wr-uc-card .thumb-wrap {
  display:flex; align-items:center; justify-content:center;
  height:100%;
}
.wr-uc-card .thumb-wrap img { max-width:100%; max-height:170px; object-fit:cover; }
.wr-uc-text { display:flex; flex-direction:column; gap:8px; }
.wr-uc-row1 {
  display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
}
.wr-uc-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:28px; letter-spacing:-0.028em; color: var(--orange);
  line-height:1; text-transform:lowercase;
}
.wr-uc-card .who {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size:24px; letter-spacing:-0.018em; color: var(--ink-dark);
  line-height:1.15; text-transform:lowercase;
}
.wr-uc-card .why {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:15px; line-height:1.5; color: var(--ink-dark-soft);
}

/* cta */
.wr-cta-grid {
  flex:1; display:grid; grid-template-columns: 0.9fr 1.1fr;
  gap:30px; margin-top:18px; min-height:0;
}
.wr-cta-left { display:flex; flex-direction:column; }
.wr-cta-headline {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:74px; line-height:0.94; letter-spacing:-0.045em;
  margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.wr-cta-headline .hl { color: var(--orange) !important; }
.wr-cta-band {
  flex:1; margin-top:28px;
  display:flex; flex-direction:column;
  justify-content: space-between; gap:8px;
}
.wr-cta-row {
  display:flex; justify-content:space-between; align-items:baseline;
  padding-bottom:14px;
  border-bottom: 1px solid rgba(10,10,10,0.12);
}
.wr-cta-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:13px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.7;
}
.wr-cta-row .val {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:20px; letter-spacing:-0.025em; color: var(--orange);
  text-transform:lowercase;
}
.wr-cta-where {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:14px; line-height:1.55; color: var(--ink-dark); opacity:0.78;
  margin-top:18px;
}
.wr-cta-right {
  display:flex; flex-direction:column;
  background: rgba(10,10,10,0.04);
  border: 1.5px solid rgba(10,10,10,0.10);
  position:relative; overflow:hidden;
}
.wr-cta-right .badge {
  position:absolute; top:14px; left:14px; z-index:2;
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:12px; letter-spacing:0.18em; text-transform:uppercase;
  padding:8px 12px; background:var(--orange); color:var(--ink-dark);
}
.wr-cta-right .stage {
  flex:1; display:flex; align-items:center; justify-content:center;
  padding:0; min-height:0;
}
.wr-cta-right .stage img { width:100%; height:100%; object-fit:cover; }
.wr-cta-right .caption {
  padding:16px 20px; border-top:1.5px solid rgba(10,10,10,0.10);
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:14px; letter-spacing:0.04em; color: var(--ink-dark);
  display:flex; justify-content:space-between; align-items:baseline;
  background: rgba(245,240,230,0.85);
}
.wr-cta-right .caption .big {
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
        '<div class="wr-foot">'
        '<span class="mark">merge<span class="dot">.</span></span>'
        f'<span class="page">{page}</span>'
        '</div>'
    )


def slide_01_cover(imgs: dict[str, Path]) -> str:
    cols_html = "".join(
        f'''<div class="col">
              <span class="brand">{w["brand"]}</span>
              <span class="mdl">{w["short"]}</span>
              <span class="price">{w["price"]}</span>
              <span class="tag">{w["tier"]}</span>
            </div>'''
        for w in BIKES
    )
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>CANYON · AEROAD · IMPORTAÇÃO BR · 2026</span>
    <div class="wr-cover-head">
      <h1 class="wr-cover-title">a <span class="hl">aero</span><br>do van der poel.</h1>
      <span class="wr-cover-cap">3 níveis · 1 frame · 42.000 → 88.000 BRL</span>
      <h2 class="wr-cover-sub">qual <span class="hl">cabe</span> no seu garage?</h2>
      <p class="wr-cover-lead">
        Mesma silhueta aero do CFR que ganha clássicas. Mudam carbono,
        groupset e wheels — não a forma do quadro. CF SL a R$ 42k. CFR a
        R$ 88k. Aqui é o que você compra por cada degrau.
      </p>
    </div>
    <div class="wr-cover-hero-single">
      <img src="file://{imgs['cfr']}" alt="canyon aeroad cfr">
    </div>
    <div class="wr-cover-ladder">{cols_html}</div>
    {_foot("01 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_02_lineup(imgs: dict[str, Path]) -> str:
    cards = [
        ("cf_sl",  "ENTRY · CF SL · 2026",
         "cf sl carbon 1.150 g · shimano 105 di2 12s · dt swiss arc 1600. mesmo aero, custo de entrada."),
        ("cf_slx", "SWEET SPOT · CF SLX · 2026",
         "cf slx carbon 1.050 g · ultegra di2 · arc 1400 50mm. 90% da CFR a 70% do preço."),
        ("cfr",    "FLAGSHIP · CFR · 2026",
         "cfr carbon 960 g (t1100/t800/m40x) · dura-ace di2 + power meter · arc 1100 62mm. mesma layup do mvdp."),
    ]
    cards_html = ""
    for slug, tag, pitch in cards:
        w = next(x for x in BIKES if x["slug"] == slug)
        cards_html += f'''<div class="wr-card">
              <div class="wr-card-img"><img src="file://{imgs[slug]}" alt="{w["full"]}"></div>
              <div class="wr-card-body">
                <div class="wr-card-head">
                  <span class="brand">{tag}</span>
                </div>
                <div class="model">{w["short"]}</div>
                <p class="pitch">{pitch}</p>
                <div class="price">{w["price"]}</div>
              </div>
            </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>LINEUP · TRÊS DEGRAUS, UM FRAME</span>
    <h2 class="wr-headline">o <span class="hl">salto</span> que importa.</h2>
    <div class="wr-lineup">{cards_html}</div>
    {_foot("02 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_03_compare(imgs: dict[str, Path]) -> str:
    rows = [
        ("carbono",      [w["frame"]    for w in BIKES], 2),  # CFR vence em peso
        ("peso (m)",     [w["weight"]   for w in BIKES], 2),
        ("groupset",     [w["groupset"] for w in BIKES], 2),
        ("wheels",       [w["wheels"]   for w in BIKES], 2),
        ("cockpit",      [w["cockpit"]  for w in BIKES], 2),
        ("preço (br)",   [w["price"]    for w in BIKES], 0),  # CF SL mais barato
    ]
    rows_html = ""
    for label, vals, hl_idx in rows:
        cells = ""
        for i, v in enumerate(vals):
            cls = " is-hl" if (hl_idx is not None and i == hl_idx) else ""
            cells += f'<span class="val{cls}">{v}</span>'
        rows_html += f'<div class="wr-cmp-row"><span class="lbl">{label}</span>{cells}</div>'

    thumbs = ""
    for w in BIKES:
        thumbs += f'''<div class="wr-cmp-thumb">
            <img src="file://{imgs[w["slug"]]}" alt="{w["short"]}">
            <span class="brand">{w["brand"]}</span>
            <span class="lbl">{w["short"]}</span>
          </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>SPEC SHEET · 3 BUILDS LADO A LADO</span>
    <h2 class="wr-headline">onde <span class="hl">cada um</span> ganha.</h2>
    <div class="wr-cmp-thumbs">
      <span></span>
      {thumbs}
    </div>
    <div class="wr-cmp-table">
      {rows_html}
    </div>
    {_foot("03 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_04_usecase(imgs: dict[str, Path]) -> str:
    cards = [
        ("cf_sl",  "primeira aero de carbono real",
         "quem está saindo de alumínio ou entry endurance e quer aero sem pagar superbike · ideal pra granfondo, fast group rides, 100 km que importa."),
        ("cf_slx", "race amador sem teto orçamentário",
         "categoria masters, ironman 70.3, granfondos competitivos · ultegra di2 + arc 1400 = mesma resposta de race da cfr em 90% dos pavimentos."),
        ("cfr",    "imprensa, pro-am, marathon mtb cross-discipline",
         "quem busca topo absoluto · carbono van der poel-spec · dura-ace di2 com power meter · arc 1100 62mm = 1 watt a menos por km a 40 km/h."),
    ]
    cards_html = ""
    for slug, who, why in cards:
        w = next(x for x in BIKES if x["slug"] == slug)
        cards_html += f'''<div class="wr-uc-card">
              <div class="thumb-wrap"><img src="file://{imgs[slug]}" alt="{w["short"]}"></div>
              <div class="wr-uc-text">
                <div class="wr-uc-row1">
                  <span class="model">{w["short"]}</span>
                  <span class="who">{who}</span>
                </div>
                <div class="why">{why}</div>
              </div>
            </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>PERFIL · PRA QUEM É CADA TIER</span>
    <h2 class="wr-headline">o <span class="hl">match</span> certo pro seu pedal.</h2>
    <div class="wr-uc-grid">{cards_html}</div>
    {_foot("04 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_05_cta(imgs: dict[str, Path]) -> str:
    hero_img = imgs["cfr"]
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>VEREDICTO MERGE · BR IMPORTAÇÃO</span>
    <div class="wr-cta-grid">
      <div class="wr-cta-left">
        <h2 class="wr-cta-headline">a <span class="hl">aero</span> da semana clássica.</h2>
        <div class="wr-cta-band">
          <div class="wr-cta-row">
            <span class="lbl">faixa de preço</span>
            <span class="val">r$ 42k → 88k</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">entrada</span>
            <span class="val">cf sl 8 · r$ 42k</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">sweet spot</span>
            <span class="val">cf slx 8 · r$ 62k</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">flagship</span>
            <span class="val">cfr di2 · r$ 88k</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">economia frame</span>
            <span class="val">90 g (cfr → cf slx)</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">aero ganhar</span>
            <span class="val">idêntico nos 3</span>
          </div>
        </div>
        <p class="wr-cta-where">
          Canyon não vende oficial no Brasil — mercado é importação direta
          (canyon.com EU/US) ou compra usada importada. Atletas BR como
          Vinícius Rangel e Wagner Iglecio rodam Aeroad. Frete + impostos
          adicionam ~80% sobre FOB.
        </p>
      </div>
      <div class="wr-cta-right">
        <span class="badge">flagship · van der poel</span>
        <div class="stage"><img src="file://{hero_img}" alt="canyon aeroad cfr"></div>
        <div class="caption">
          <span>aeroad cfr · di2</span>
          <span class="big">r$ 88k</span>
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

    print("→ preparando imagens (single hero shot pros 3 tiers)...")
    imgs: dict[str, Path] = {}
    # mesma foto pros 3 tiers (Aeroad shape idêntica nos 3 níveis)
    src = BIKES[0]["img"]
    if not src.exists():
        raise FileNotFoundError(f"falta press shot: {src}")
    dest = TMP / "aeroad_hero.png"
    strip_bg(src, dest)
    for b in BIKES:
        imgs[b["slug"]] = dest

    slides = [
        ("01_cover",   slide_01_cover(imgs)),
        ("02_lineup",  slide_02_lineup(imgs)),
        ("03_compare", slide_03_compare(imgs)),
        ("04_usecase", slide_04_usecase(imgs)),
        ("05_cta",     slide_05_cta(imgs)),
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
            page.screenshot(
                path=str(png), full_page=False, omit_background=False,
                clip={"x": 0, "y": 0, "width": W, "height": H},
            )
            print(f"  ✓ {png.name}")
        browser.close()


if __name__ == "__main__":
    main()
