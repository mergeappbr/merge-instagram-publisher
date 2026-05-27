"""Carrossel comparativo: Fitbit Charge 6 × Whoop MG.

5 slides 1080x1350 cream — segue EXATAMENTE o mesmo design system do
render_wearables_2026.py (.wr-* classes), adaptado pra 2 produtos em vez
de 3. Sem 'vs' decorativo, CTA com left-bullets + right-hero-card igual
aos outros reviews.

Press shots: brand/images/FitbitGoogle.webp + brand/whoop/whoop5_onyx.png

Output: output/posts/fitbit_vs_whoop/01_cover.png ... 05_cta.png
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_STYLES = (ROOT / "templates" / "posts" / "_post_styles.css").read_text(encoding="utf-8")

W, H = 1080, 1350
SLUG = "fitbit_vs_whoop"
OUT = ROOT / "output" / "posts" / SLUG
TMP = ROOT / ".tmp_posts" / SLUG

WHITE_THRESHOLD = 240
TOLERANCE = 22


def strip_white_bg(src: Path, dest: Path, *, aggressive: bool = False) -> Path:
    """Strip background → transparent.

    Modo padrão: white_threshold + bg médio dos cantos (spread<14).
    Modo aggressive: flood-fill começando dos 4 cantos com tolerance alta.
    Bom pra fotos com bg gradiente leve (azulado/cream claro).
    """
    img = Image.open(src).convert("RGBA")
    pixels = img.load()
    w, h = img.size

    if aggressive:
        # Passagem global pixel-a-pixel — sem propagação, evita invadir interior
        # do produto. Usa avg dos cantos só pra detectar cor de fundo (não bg
        # gradiente forte). Pega pixels claros (>= ~200) que matchem cor do bg
        # OU sejam quase brancos. Cor do band (red/blue/black) fica intacta.
        corner_samples = [pixels[2, 2], pixels[w-3, 2], pixels[2, h-3], pixels[w-3, h-3],
                          pixels[w//2, 2], pixels[w//2, h-3]]
        avg = (sum(c[0] for c in corner_samples) // len(corner_samples),
               sum(c[1] for c in corner_samples) // len(corner_samples),
               sum(c[2] for c in corner_samples) // len(corner_samples))
        TOL_AGG = 40
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                if a == 0: continue
                # bg azulado claro (R~G~B>180 e perto do avg dos cantos)
                bg_match = (abs(r - avg[0]) <= TOL_AGG and abs(g - avg[1]) <= TOL_AGG
                            and abs(b - avg[2]) <= TOL_AGG and r >= 180 and g >= 180 and b >= 180)
                # ou quase branco puro
                near_white = (r >= 230 and g >= 230 and b >= 230)
                if bg_match or near_white:
                    pixels[x, y] = (r, g, b, 0)
    else:
        samples = [
            pixels[2, 2], pixels[w // 2, 2], pixels[w - 3, 2],
            pixels[2, h // 2], pixels[w - 3, h // 2],
            pixels[2, h - 3], pixels[w // 2, h - 3], pixels[w - 3, h - 3],
        ]
        rs = [s[0] for s in samples]; gs = [s[1] for s in samples]; bs = [s[2] for s in samples]
        spread = max(max(rs) - min(rs), max(gs) - min(gs), max(bs) - min(bs))
        bg = None
        if spread < 14:
            bg = (sum(rs) // len(rs), sum(gs) // len(gs), sum(bs) // len(bs))
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                if a == 0: continue
                if r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD:
                    pixels[x, y] = (r, g, b, 0); continue
                if bg is not None:
                    if (abs(r - bg[0]) <= TOLERANCE and abs(g - bg[1]) <= TOLERANCE
                            and abs(b - bg[2]) <= TOLERANCE):
                        pixels[x, y] = (r, g, b, 0)
    bbox = img.getbbox()
    if bbox: img = img.crop(bbox)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")
    return dest


# 2 produtos head-to-head.
# crop_box: (left, top, right, bottom) — recorta uma unidade isolada pra
# não cortar bracelets nas bordas dos cards. Coordenadas absolutas da source.
WEARABLES = [
    {
        "slug": "fitbit",
        "short": "fitbit charge 6",
        "brand": "fitbit",
        "full":  "google fitbit charge 6",
        "form":  "tracker amoled",
        "bat":   "7 dias",
        "gps":   "built-in single",
        "ecg":   "sim · pulso",
        "sens":  "fc · spo2 · ecg · eda",
        "sub":   "premium r$ 19,90/mês opcional",
        "price_us": "us$ 159 (uma vez)",
        "price":  "r$ 1.499",
        "year":  "2023",
        "tier":  "tracker premium · 2023",
        "img":   ROOT / "brand" / "images" / "FitbitGoogle.webp",
        "aggressive_strip": True,           # bg azulado gradiente — strip pixel-wise
    },
    {
        "slug": "whoop",
        "short": "whoop mg",
        "brand": "whoop",
        "full":  "whoop mg medical grade",
        "form":  "strap · sem tela",
        "bat":   "14 dias",
        "gps":   "via iphone",
        "ecg":   "medical grade",
        "sens":  "fc 26x/s · hrv · ecg · bp · temp",
        # Whoop tem 2 custos reais: 1) device físico, 2) assinatura anual
        # obrigatória — sem ela o aparelho não funciona. Pedro pediu pra
        # mostrar os dois separados na ladder/CTA.
        "sub":   "whoop life us$ 359/ano (obrigatório)",
        "price_us": "device us$ 0 (incluso) + us$ 359/ano",
        "price":  "device incluso · ≈ r$ 2.500/ano",
        "year":  "2025",
        "tier":  "subscription medical · 2025",
        "img":   ROOT / "brand" / "whoop" / "whoop5_onyx.png",
        "crop_box": (440, 0, 920, 984),     # strap central frontal (1380x984)
    },
]


# ---------- CSS (SISTEMA wr-* idêntico ao wearables_2026) ----------

CSS = """
.wr-root { display:flex; flex-direction:column; height:100%; padding-bottom: 24px; }

/* watermark footer com respiro extra do rodapé */
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

/* kicker bumped */
.post .kicker {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:18px !important; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.78;
}

/* ===== COVER ===== */
.wr-cover-head { display:flex; flex-direction:column; gap:0; }
.wr-cover-title {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:128px; line-height:0.86; letter-spacing:-0.055em;
  margin:18px 0 0; text-transform:lowercase; color: var(--ink-dark);
}
.wr-cover-title .hl { color: var(--orange); }
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
.wr-cover-sub .hl, .wr-cover-sub > .hl { color: var(--orange) !important; }
.wr-cover-lead {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:22px; line-height:1.5; color: var(--ink-dark);
  margin:18px 0 0; max-width: 920px;
}

/* hero centralizado · 2-up (sem 'vs', deixa visual falar) */
.wr-cover-hero {
  flex:1; display:grid; grid-template-columns: 1fr 1fr;
  align-items:center; justify-items:center;
  gap:20px; margin-top:18px; min-height:0;
}
.wr-cover-hero .device {
  display:flex; align-items:center; justify-content:center;
  width:100%; height:100%;
}
.wr-cover-hero .device img { max-width:100%; max-height:100%; object-fit:contain; }

/* escada de preços (2 cols) */
.wr-cover-ladder {
  margin-top:22px; padding-top:18px;
  display:grid; grid-template-columns: 1fr 1fr; gap:32px;
  border-top: 2px solid rgba(10,10,10,0.14);
}
.wr-cover-ladder .col {
  display:flex; flex-direction:column; gap:6px;
  padding-right:24px;
  border-right: 1px solid rgba(10,10,10,0.10);
}
.wr-cover-ladder .col:last-child { border-right:0; padding-right:0; }
.wr-cover-ladder .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:13px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.62;
}
.wr-cover-ladder .mdl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:30px; letter-spacing:-0.022em;
  text-transform:lowercase; color: var(--ink-dark); line-height:1.04;
  min-height: 2.2em;
  display:flex; align-items:flex-start;
}
.wr-cover-ladder .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:32px; letter-spacing:-0.02em; color: var(--orange); line-height:1.08;
  margin-top:2px;
}
.wr-cover-ladder .price-sub {
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:13px; letter-spacing:0.06em; color: var(--ink-dark); opacity:0.62;
  margin-top:4px; line-height:1.3;
}

/* ===== LINEUP / HEADLINES ===== */
.wr-headline {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:64px; line-height:1.0; letter-spacing:-0.035em;
  margin:14px 0 22px; text-transform:lowercase; color: var(--ink-dark);
}
.wr-headline .hl { color: var(--orange) !important; }

.wr-lineup {
  flex:1; display:grid; grid-template-columns: 1fr 1fr;
  gap:22px; margin-top:6px; margin-bottom:14px; min-height:0;
}
.wr-card {
  display:grid; grid-template-rows: 1fr auto;
  background: rgba(10,10,10,0.035);
  border: 1.5px solid rgba(10,10,10,0.10);
  overflow:hidden; min-height:0;
}
.wr-card-img {
  display:flex; align-items:center; justify-content:center;
  padding:18px; min-height:0;
}
.wr-card-img img { max-width: 100%; max-height: 100%; object-fit: contain; }
.wr-card-body { padding:20px 26px 24px; border-top:1.5px solid rgba(10,10,10,0.10); }
.wr-card-head {
  display:flex; align-items:baseline; justify-content:space-between; gap:10px;
  margin-bottom:8px;
}
.wr-card .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:13px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:32px; line-height:0.96; letter-spacing:-0.028em;
  color: var(--ink-dark); text-transform:lowercase;
}
.wr-card .model .hl { color: var(--orange); }
.wr-card .pitch {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:14px; line-height:1.5; color: var(--ink-dark-soft);
  margin:10px 0 0;
}
.wr-card .price {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:32px; letter-spacing:-0.025em; color: var(--orange);
  margin-top:12px;
}

/* ===== COMPARE (spec sheet 2 cols) ===== */
.wr-cmp-thumbs {
  display:grid; grid-template-columns: 1.05fr 1fr 1fr;
  align-items:end; gap:0; padding-bottom:14px;
  border-bottom: 2px solid rgba(10,10,10,0.14);
}
.wr-cmp-thumb { display:flex; flex-direction:column; align-items:center; gap:6px; }
.wr-cmp-thumb img { width: 180px; height: 130px; object-fit: contain; }
.wr-cmp-thumb .brand {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:12px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-cmp-thumb .lbl {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:22px; letter-spacing:-0.02em; color: var(--ink-dark);
  text-transform:lowercase; text-align:center; line-height:1.05;
}
.wr-cmp-table {
  flex:1; display:flex; flex-direction:column; min-height:0; margin-top:0;
}
.wr-cmp-row {
  flex:1;
  display:grid; grid-template-columns: 1.05fr 1fr 1fr;
  align-items:center;
  padding:12px 0;
  border-bottom: 1px solid rgba(10,10,10,0.08);
}
.wr-cmp-row .lbl {
  font-family:'JetBrains Mono', monospace; font-weight:700;
  font-size:14px; letter-spacing:0.16em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.65;
}
.wr-cmp-row .val {
  font-family:'Inter', sans-serif; font-weight:700;
  font-size:18px; letter-spacing:-0.01em;
  color: var(--ink-dark); line-height:1.2; text-align:center;
}
.wr-cmp-row .val.is-hl { color: var(--orange); font-weight:900; }
.wr-cmp-row .val.is-mute { opacity:0.4; }

/* ===== USE-CASE ===== */
.wr-uc-grid {
  flex:1; display:flex; flex-direction:column; gap:22px;
  margin-top:10px; margin-bottom:10px; min-height:0;
}
.wr-uc-card {
  flex:1; display:grid; grid-template-columns: 220px 1fr;
  gap:30px; align-items:center;
  padding:22px 30px;
  background: rgba(10,10,10,0.035);
  border-left: 5px solid var(--orange);
  min-height:0;
}
.wr-uc-card .thumb-wrap {
  display:flex; align-items:center; justify-content:center;
  height:100%;
}
.wr-uc-card .thumb-wrap img { max-width:100%; max-height:200px; object-fit:contain; }
.wr-uc-text { display:flex; flex-direction:column; gap:10px; }
.wr-uc-row1 {
  display:flex; align-items:baseline; gap:16px; flex-wrap:wrap;
}
.wr-uc-card .model {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:34px; letter-spacing:-0.028em; color: var(--orange);
  line-height:1; text-transform:lowercase;
}
.wr-uc-card .who {
  font-family:'Inter', sans-serif; font-weight:800;
  font-size:28px; letter-spacing:-0.018em; color: var(--ink-dark);
  line-height:1.15; text-transform:lowercase;
}
.wr-uc-card .why {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:17px; line-height:1.55; color: var(--ink-dark-soft);
}

/* ===== CTA (left bullets + right hero card — IDÊNTICO aos outros reviews) ===== */
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
  font-size:14px; letter-spacing:0.18em; text-transform:uppercase;
  color: var(--ink-dark); opacity:0.7;
}
.wr-cta-row .val {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:22px; letter-spacing:-0.025em; color: var(--orange);
  text-transform:lowercase;
}
.wr-cta-where {
  font-family:'JetBrains Mono', monospace; font-weight:500;
  font-size:15px; line-height:1.55; color: var(--ink-dark); opacity:0.78;
  margin-top:20px;
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
  padding:18px; min-height:0;
}
.wr-cta-right .stage img { max-width:100%; max-height:100%; object-fit:contain; }
.wr-cta-right .caption {
  padding:16px 20px; border-top:1.5px solid rgba(10,10,10,0.10);
  font-family:'JetBrains Mono', monospace; font-weight:600;
  font-size:14px; letter-spacing:0.04em; color: var(--ink-dark);
  display:flex; justify-content:space-between; align-items:baseline;
}
.wr-cta-right .caption .big {
  font-family:'Inter', sans-serif; font-weight:900;
  font-size:24px; letter-spacing:-0.02em; color: var(--orange);
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


# ---------- SLIDES ----------

def slide_01_cover(imgs: dict[str, Path]) -> str:
    # Cada produto tem 1 linha principal de preço + sub-linha opcional (tipo
    # contexto: 'único pagamento', 'device incluso + anuidade').
    ladder_meta = {
        "fitbit": ("r$ 1.499", "único pagamento"),
        "whoop":  ("≈ r$ 2.500", "device incluso + anuidade"),
    }
    cols_html = ""
    for w in WEARABLES:
        price, sub = ladder_meta[w["slug"]]
        cols_html += f'''<div class="col">
              <span class="brand">{w["brand"]}</span>
              <span class="mdl">{w["short"]}</span>
              <span class="price">{price}</span>
              <span class="price-sub">{sub}</span>
            </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>WEARABLES · TELA OU SILÊNCIO · 2026</span>
    <div class="wr-cover-head">
      <h1 class="wr-cover-title">tracker<br>ou <span class="hl">strap</span>.</h1>
      <span class="wr-cover-cap">2 pactos · 1 pulso · 1.499 → 2.500/ano brl</span>
      <h2 class="wr-cover-sub">qual te <span class="hl">mede</span> melhor?</h2>
      <p class="wr-cover-lead">
        Fitbit te entrega tela, Google Maps e ECG no pulso por 1 pagamento.
        Whoop te entrega 26 leituras de FC por segundo, medical grade e
        sem distração, 24/7 — em assinatura. Mesma promessa, dois corpos.
      </p>
    </div>
    <div class="wr-cover-hero">
      <div class="device"><img src="file://{imgs["fitbit"]}" alt="fitbit charge 6"></div>
      <div class="device"><img src="file://{imgs["whoop"]}" alt="whoop mg"></div>
    </div>
    <div class="wr-cover-ladder">{cols_html}</div>
    {_foot("01 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_02_lineup(imgs: dict[str, Path]) -> str:
    cards = [
        ("fitbit", "TRACKER · GOOGLE · 2023",
         "amoled touchscreen 1,04\" · gps built-in · ecg + spo2 + eda · google maps, wallet, yt music no pulso. premium é opcional."),
        ("whoop",  "STRAP MEDICAL · WHOOP · 2025",
         "sem tela, 14 dias bateria · ecg medical grade · blood pressure · whoop age + healthspan. assinatura é o pacto."),
    ]
    cards_html = ""
    for slug, tag, pitch in cards:
        w = next(x for x in WEARABLES if x["slug"] == slug)
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
    <span class="kicker"><span class="dot"></span>LINEUP · DOIS CORPOS, DUAS FILOSOFIAS</span>
    <h2 class="wr-headline">um pulso, <span class="hl">duas</span> apostas.</h2>
    <div class="wr-lineup">{cards_html}</div>
    {_foot("02 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_03_compare(imgs: dict[str, Path]) -> str:
    rows = [
        ("forma",        [w["form"]   for w in WEARABLES], None),
        ("bateria",      [w["bat"]    for w in WEARABLES], 1),    # whoop 14d
        ("gps",          [w["gps"]    for w in WEARABLES], 0),    # fitbit built-in
        ("ecg",          [w["ecg"]    for w in WEARABLES], 1),    # whoop medical
        ("sensores",     [w["sens"]   for w in WEARABLES], 1),    # whoop mais
        ("subscription", [w["sub"]    for w in WEARABLES], 0),    # fitbit opcional
        ("preço (br)",   [w["price"]  for w in WEARABLES], 0),    # fitbit single payment
    ]
    rows_html = ""
    for label, vals, hl_idx in rows:
        cells = ""
        for i, v in enumerate(vals):
            cls = ""
            if hl_idx is not None and i == hl_idx: cls = " is-hl"
            elif v == "—": cls = " is-mute"
            cells += f'<span class="val{cls}">{v}</span>'
        rows_html += f'<div class="wr-cmp-row"><span class="lbl">{label}</span>{cells}</div>'

    thumbs = ""
    for w in WEARABLES:
        thumbs += f'''<div class="wr-cmp-thumb">
            <img src="file://{imgs[w["slug"]]}" alt="{w["short"]}">
            <span class="brand">{w["brand"]}</span>
            <span class="lbl">{w["short"]}</span>
          </div>'''
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>SPEC SHEET · COMPARATIVO TÉCNICO</span>
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
        ("fitbit", "tracker premium pra rotina",
         "5-10k, academia e dormir com relógio · pace, fc e notificação no pulso · ecg + spo2 de checagem · sem mensalidade obrigatória · pra quem quer wearable que paga uma vez e some."),
        ("whoop",  "métrica clínica · 24/7 · sem distração",
         "performance + longevidade · readiness, sleep, heart screener com ecg medical grade · pra quem encara wearable como serviço médico contínuo · assinatura é o compromisso."),
    ]
    cards_html = ""
    for slug, who, why in cards:
        w = next(x for x in WEARABLES if x["slug"] == slug)
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
    <span class="kicker"><span class="dot"></span>PERFIL DE USO · PRA QUEM É CADA UM</span>
    <h2 class="wr-headline">o <span class="hl">match</span> certo pro seu pulso.</h2>
    <div class="wr-uc-grid">{cards_html}</div>
    {_foot("04 / 05")}
  </div>
</div>"""
    return _shell(body)


def slide_05_cta(imgs: dict[str, Path]) -> str:
    # hero direita = fitbit (single payment, padrão "veredicto barato")
    hero_img = imgs["fitbit"]
    body = f"""
<div class="post post--light">
  <div class="post__content wr-root">
    <span class="kicker"><span class="dot"></span>VEREDICTO MERGE</span>
    <div class="wr-cta-grid">
      <div class="wr-cta-left">
        <h2 class="wr-cta-headline">o <span class="hl">pacto</span> certo pro seu pulso.</h2>
        <div class="wr-cta-band">
          <div class="wr-cta-row">
            <span class="lbl">fitbit · pagamento único</span>
            <span class="val">r$ 1.499 (1×)</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">whoop · device</span>
            <span class="val">incluso na assinatura</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">whoop · anuidade obrig.</span>
            <span class="val">us$ 359/ano (≈ r$ 2.500)</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">ecg medical grade</span>
            <span class="val">só whoop mg</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">tela + google apps</span>
            <span class="val">só charge 6</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">5 anos · custo total</span>
            <span class="val">fitbit r$ 1.499 vs whoop ≈ 12.500</span>
          </div>
          <div class="wr-cta-row">
            <span class="lbl">ano</span>
            <span class="val">2023 → 2025</span>
          </div>
        </div>
        <p class="wr-cta-where">
          Fitbit: importação direta Google Store US (~us$ 159) ou revenda Mercado Livre BR.
          Whoop MG: whoop.com plano anual Whoop Life, device incluso, ativação 7-14 dias.
        </p>
      </div>
      <div class="wr-cta-right">
        <span class="badge">single payment</span>
        <div class="stage"><img src="file://{hero_img}" alt="fitbit charge 6"></div>
        <div class="caption">
          <span>charge 6 · obsidian</span>
          <span class="big">r$ 1.499</span>
        </div>
      </div>
    </div>
    {_foot("05 / 05")}
  </div>
</div>"""
    return _shell(body)


# ---------- RENDER ----------

def main() -> None:
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    if TMP.exists(): shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)

    print("→ preparando imagens (strip BG + normalize visual)...")
    imgs: dict[str, Path] = {}
    CANVAS = 1400
    TARGET_SUBJECT_H = 1100
    for w in WEARABLES:
        src = w["img"]
        if not src.exists():
            raise FileNotFoundError(f"falta press shot: {src}")
        # Crop manual ANTES do strip — pega 1 unidade isolada da source.
        crop_box = w.get("crop_box")
        if crop_box:
            cropped = TMP / f"{w['slug']}_cropped.png"
            src_img = Image.open(src).convert("RGBA")
            src_img.crop(crop_box).save(cropped, "PNG")
            src = cropped
        dest = TMP / f"{w['slug']}.png"
        strip_white_bg(src, dest, aggressive=w.get("aggressive_strip", False))
        img = Image.open(dest).convert("RGBA")
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                if a < 24: px[x, y] = (0, 0, 0, 0)
        bbox = img.getbbox()
        if bbox: img = img.crop(bbox)
        ratio = TARGET_SUBJECT_H / img.height
        new_w = int(img.width * ratio)
        new_h = TARGET_SUBJECT_H
        img = img.resize((new_w, new_h), Image.LANCZOS)
        if new_w > CANVAS:
            img.thumbnail((CANVAS, CANVAS), Image.LANCZOS)
        canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        ox = (CANVAS - img.width) // 2
        oy = (CANVAS - img.height) // 2
        canvas.paste(img, (ox, oy), img)
        canvas.save(dest, "PNG", optimize=True)
        imgs[w["slug"]] = dest
        print(f"  ✓ {w['slug']} (subject {img.width}×{img.height})")

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
