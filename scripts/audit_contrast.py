#!/usr/bin/env python3
"""
Auditoria de contraste WCAG sobre os PNGs renderizados (output/feed/*.png).

Heurística: amostra 3 zonas onde texto da arte fica (headline, lead, footer
no canvas 1080x1350) e calcula contraste estimado de texto BRANCO (#F2F2F2)
sobre a luminância média da zona. Não é OCR — é proxy direcional pra dizer
"essas fotos estão escuras demais ou claras demais nas regiões de texto".

WCAG 2.1: contraste mínimo 4.5:1 pra texto normal AA, 3:1 pra large text.

Uso:
    python3 scripts/audit_contrast.py
    python3 scripts/audit_contrast.py --threshold 4.5
    python3 scripts/audit_contrast.py --dir output/feed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("PIL/Pillow ausente. pip install Pillow")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "output" / "feed"

# Cor do texto principal definida em tokens.css (--ink)
INK = (0xF2, 0xF2, 0xF2)
INK_SOFT = (0xC9, 0xC9, 0xC9)


def srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    r, g, b = (srgb_to_linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    l1, l2 = luminance(c1), luminance(c2)
    lo, hi = min(l1, l2), max(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def average_rgb(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    crop = img.crop(box).convert("RGB")
    pixels = list(crop.getdata())
    n = len(pixels)
    if n == 0:
        return (0, 0, 0)
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return (r, g, b)


# Zonas (px) no canvas 1080x1350 onde texto costuma cair em cada template
# Convenção: (x0, y0, x1, y1)
ZONES = {
    "headline": (80, 480, 1000, 900),   # banda principal de headline
    "lead":     (80, 920, 1000, 1180),  # corpo abaixo da headline
    "footer":   (80, 1240, 1000, 1320), # rodapé
}


def audit(path: Path) -> dict:
    img = Image.open(path)
    w, h = img.size
    if (w, h) != (1080, 1350):
        # escala proporcional pras zonas — útil se rodar em PNGs em outro tamanho
        scale_x = w / 1080
        scale_y = h / 1350
        zones = {
            name: (
                int(x0 * scale_x), int(y0 * scale_y),
                int(x1 * scale_x), int(y1 * scale_y),
            )
            for name, (x0, y0, x1, y1) in ZONES.items()
        }
    else:
        zones = ZONES

    result: dict = {"file": path.name, "size": f"{w}x{h}", "zones": {}}
    for name, box in zones.items():
        bg_avg = average_rgb(img, box)
        ratio_ink = contrast_ratio(INK, bg_avg)
        ratio_soft = contrast_ratio(INK_SOFT, bg_avg)
        result["zones"][name] = {
            "bg_rgb": bg_avg,
            "ratio_ink": round(ratio_ink, 2),
            "ratio_soft": round(ratio_soft, 2),
        }
    # menor contraste de cada texto entre as 3 zonas (worst case)
    result["min_ratio_ink"] = min(z["ratio_ink"] for z in result["zones"].values())
    result["min_ratio_soft"] = min(z["ratio_soft"] for z in result["zones"].values())
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default=str(DEFAULT_DIR))
    p.add_argument("--threshold", type=float, default=4.5,
                   help="contraste mínimo AA (default 4.5)")
    p.add_argument("--use-soft", action="store_true",
                   help="avalia ink-soft (#C9C9C9) em vez de ink (#F2F2F2)")
    args = p.parse_args()

    folder = Path(args.dir)
    if not folder.exists():
        sys.exit(f"pasta não existe: {folder}")

    pngs = sorted(folder.glob("*.png"))
    if not pngs:
        sys.exit(f"nenhum PNG em {folder}")

    print(f"Auditando {len(pngs)} PNGs em {folder}")
    print(f"Threshold WCAG AA: {args.threshold}:1")
    print(f"Texto avaliado: {'INK_SOFT (#C9C9C9 lead)' if args.use_soft else 'INK (#F2F2F2 headline)'}")
    print("-" * 80)

    results = [audit(p) for p in pngs]
    key = "min_ratio_soft" if args.use_soft else "min_ratio_ink"

    fails = [r for r in results if r[key] < args.threshold]
    fails.sort(key=lambda r: r[key])

    print(f"\n❌ {len(fails)} POSTS FALHAM AA ({args.threshold}:1):\n")
    for r in fails:
        worst_zone = min(r["zones"].items(), key=lambda kv: kv[1]["ratio_ink" if not args.use_soft else "ratio_soft"])
        zone_name, zone_data = worst_zone
        ratio_key = "ratio_soft" if args.use_soft else "ratio_ink"
        print(
            f"  {r['file']:<45} {r[key]:>5.2f}:1  "
            f"(pior: {zone_name} bg={zone_data['bg_rgb']})"
        )

    okays = len(results) - len(fails)
    print(f"\n✅ {okays}/{len(results)} posts passam.")
    print(f"📊 distribuição min_ratio_ink:")
    bins = [0, 3, 4.5, 7, 21]
    for lo, hi in zip(bins, bins[1:]):
        n = sum(1 for r in results if lo <= r["min_ratio_ink"] < hi)
        bar = "█" * n
        print(f"  [{lo:>4.1f} – {hi:>4.1f}): {n:>3} {bar}")


if __name__ == "__main__":
    main()
