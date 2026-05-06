"""
Merge Creator — image_bank: busca paralela em Unsplash + Pexels.

Setup:
  export UNSPLASH_ACCESS_KEY=...
  export PEXELS_API_KEY=...

Uso:
  python3 src/image_bank.py "trail running portugal" --count 10
  python3 src/image_bank.py "open water swim sunrise" --count 8 --orientation portrait

Saída: brand/images/_bank/<slug>/{unsplash,pexels}/*.jpg + index.json com créditos.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "brand" / "images" / "_bank"

UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")

UA = "Mozilla/5.0 MergeCreator/1.0"


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "_", text).strip("_")


def http_get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def http_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        f.write(r.read())


# ---------------- Unsplash ----------------
def unsplash_search(query: str, count: int, orientation: str = "portrait") -> list[dict]:
    if not UNSPLASH_KEY:
        return []
    params = {
        "query": query,
        "per_page": min(count, 30),
        "orientation": orientation,
        "content_filter": "high",
    }
    url = f"https://api.unsplash.com/search/photos?{urllib.parse.urlencode(params)}"
    data = http_get_json(url, {"Authorization": f"Client-ID {UNSPLASH_KEY}"})
    out = []
    for p in data.get("results", []):
        out.append({
            "source": "unsplash",
            "id": p["id"],
            "url": p["urls"]["regular"],
            "url_full": p["urls"]["full"],
            "width": p["width"],
            "height": p["height"],
            "credit_name": p["user"]["name"],
            "credit_url": p["user"]["links"]["html"],
            "page_url": p["links"]["html"],
            "alt": p.get("alt_description") or query,
        })
    return out


# ---------------- Pexels ----------------
def pexels_search(query: str, count: int, orientation: str = "portrait") -> list[dict]:
    if not PEXELS_KEY:
        return []
    params = {
        "query": query,
        "per_page": min(count, 80),
        "orientation": orientation,
    }
    url = f"https://api.pexels.com/v1/search?{urllib.parse.urlencode(params)}"
    data = http_get_json(url, {"Authorization": PEXELS_KEY})
    out = []
    for p in data.get("photos", []):
        out.append({
            "source": "pexels",
            "id": str(p["id"]),
            "url": p["src"]["large2x"],
            "url_full": p["src"]["original"],
            "width": p["width"],
            "height": p["height"],
            "credit_name": p["photographer"],
            "credit_url": p["photographer_url"],
            "page_url": p["url"],
            "alt": p.get("alt") or query,
        })
    return out


# ---------------- Pipeline ----------------
def search(query: str, count: int = 10, orientation: str = "portrait") -> dict:
    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        f_uns = ex.submit(unsplash_search, query, count, orientation)
        f_pex = ex.submit(pexels_search, query, count, orientation)
        unsplash = f_uns.result()
        pexels = f_pex.result()
    return {"query": query, "unsplash": unsplash, "pexels": pexels}


def download(query: str, count: int = 10, orientation: str = "portrait") -> Path:
    if not UNSPLASH_KEY and not PEXELS_KEY:
        raise SystemExit("Defina UNSPLASH_ACCESS_KEY e/ou PEXELS_API_KEY no ambiente.")

    results = search(query, count, orientation)
    slug = slugify(query)
    out_dir = BANK / slug
    (out_dir / "unsplash").mkdir(parents=True, exist_ok=True)
    (out_dir / "pexels").mkdir(parents=True, exist_ok=True)

    index = {"query": query, "orientation": orientation, "items": []}

    def fetch(item):
        sub = out_dir / item["source"] / f"{item['id']}.jpg"
        if not sub.exists():
            try:
                http_download(item["url"], sub)
            except Exception as e:
                return {"error": str(e), **item}
        return {"path": str(sub.relative_to(ROOT)), **item}

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        items = list(ex.map(fetch, results["unsplash"] + results["pexels"]))

    index["items"] = items
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2))

    print(f"→ {len(results['unsplash'])} unsplash + {len(results['pexels'])} pexels em {out_dir.relative_to(ROOT)}")
    print(f"   index: {(out_dir / 'index.json').relative_to(ROOT)}")
    return out_dir


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Busca + baixa imagens em Unsplash e Pexels.")
    parser.add_argument("query", help='Termo de busca (use aspas: "trail running")')
    parser.add_argument("--count", type=int, default=10, help="Quantidade por banco (default 10)")
    parser.add_argument("--orientation", default="portrait", choices=["portrait", "landscape", "squarish", "square"])
    args = parser.parse_args(argv)

    download(args.query, args.count, args.orientation)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
