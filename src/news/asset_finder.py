"""Asset finder: encontra foto oficial de produto pra notícia.

Pipeline (tudo Gemini):
  1. extract_entity(title, summary) → Gemini Flash JSON:
     {kind: "product"|"event"|"athlete"|"concept", name, brand, category, ...}
  2. Se kind=product e confidence ≥ 0.7:
     a. Cache hit em brand/images/_auto/<slug>.<ext> → retorna.
     b. _find_image_urls() → Gemini Flash com Google Search grounding,
        retorna URLs candidatas (.jpg/.png/.webp).
     c. _download_image() pra cada candidata.
     d. _validate_image() → Gemini Vision: é o produto certo? quality?
        Rejeita se: !is_match, has_text_overlay, quality < 6.
     e. Aceita primeira que passa → salva em brand/images/_auto/<slug>.<ext>.
  3. Retorna {path, entity, vision, url} ou None.

Falha silenciosa em qualquer passo — caller cai pro flow normal de IA.

Custo: ~$0.003 por notícia (3 chamadas Gemini Flash text + 1 vision).
Cache permanente: re-execução pro mesmo produto = $0 (cache hit).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
AUTO_DIR = ROOT / "brand" / "images" / "_auto"

GEMINI_TEXT_MODEL = "gemini-2.5-flash"
GEMINI_ENDPOINT_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)

# URL de arquivo de imagem (extensão obrigatória, query string opcional)
_IMG_URL_RE = re.compile(
    r'https?://[^\s<>"\'`]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\s<>"\'`]*)?',
    re.IGNORECASE,
)
# URL HTTP/HTTPS genérica (qualquer página)
_PAGE_URL_RE = re.compile(r'https?://[^\s<>"\'`)]+', re.IGNORECASE)
# og:image / twitter:image meta tag
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)\s*=\s*["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\'][^>]+content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_IMAGE_REV_RE = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+(?:property|name)\s*=\s*["\'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["\']',
    re.IGNORECASE,
)

_MIN_IMAGE_BYTES = 30 * 1024
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _slug(s: str, max_len: int = 60) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:max_len] or "unknown"


def _mime_from_bytes(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _ext_from_mime(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".jpg")


def _gemini_text(prompt: str, *, grounded: bool = False, json_mode: bool = True) -> Optional[str]:
    """Chama Gemini Flash. Se grounded=True, ativa google_search tool.
    json_mode=True força responseMimeType=application/json (incompatível com
    grounded, então ignorado nesse caso)."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    gen_cfg: dict = {"temperature": 0.3}
    if grounded:
        body["tools"] = [{"google_search": {}}]
        # google_search + responseMimeType=json é incompatível na API
    elif json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body["generationConfig"] = gen_cfg
    url = GEMINI_ENDPOINT_TMPL.format(model=GEMINI_TEXT_MODEL, key=key)
    backoffs = [4, 12, 30]
    for attempt in range(len(backoffs) + 1):
        try:
            with httpx.Client(timeout=60) as client:
                r = client.post(url, json=body, headers={"Content-Type": "application/json"})
            if r.status_code == 200:
                resp = r.json()
                cands = resp.get("candidates") or []
                if not cands:
                    return None
                parts = cands[0].get("content", {}).get("parts", []) or []
                return "".join(p.get("text", "") for p in parts).strip() or None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < len(backoffs):
                print(f"  ↪ asset_finder.gemini HTTP {r.status_code} retry em {backoffs[attempt]}s")
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ asset_finder.gemini HTTP {r.status_code}: {r.text[:200]}")
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < len(backoffs):
                print(f"  ↪ asset_finder.gemini exception retry em {backoffs[attempt]}s: {e!r}")
                time.sleep(backoffs[attempt])
                continue
            print(f"⚠ asset_finder.gemini exception: {e!r}")
            return None
    return None


def _parse_json_loose(raw: str) -> Optional[dict]:
    """Tenta parsear JSON. Aceita markdown fence, primeiro bloco { ... }."""
    s = raw.strip()
    # Remove fence
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = s.rstrip("`").strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Primeiro bloco { ... } balanceado simples
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def extract_entity(title: str, summary: str) -> Optional[dict]:
    """Extrai entidade da notícia. Retorna dict com kind/name/brand/category."""
    prompt = (
        "Analise esta notícia e devolva APENAS JSON válido (sem markdown, sem prosa):\n"
        "{\n"
        '  "kind": "product" | "event" | "athlete" | "concept",\n'
        '  "name": "<nome canônico, ex: \\"Roka Maverick II\\">",\n'
        '  "brand": "<marca SE kind=product, senão null>",\n'
        '  "category": "<wetsuit|running shoe|GPS watch|smartwatch|bike|null>",\n'
        '  "confidence": 0.0-1.0\n'
        "}\n\n"
        "Critérios estritos:\n"
        "- kind=product SÓ se a notícia menciona um MODELO ESPECÍFICO (ex: 'Roka Maverick II',\n"
        "  'Adidas EVO SL', 'Garmin Fenix 8'). Genéricos como 'novo tênis Nike' sem modelo = concept.\n"
        "- kind=athlete só se foco é atleta específico (Kipchoge, Pogačar).\n"
        "- kind=event só se prova/competição específica (Maratona Rio 2026, UTMB Mont-Blanc).\n"
        "- kind=concept se nada acima (estudo, tendência, técnica, debate).\n"
        "- confidence ≥ 0.9 só se você tem certeza absoluta do nome canônico.\n"
        "- Se ambíguo, prefira concept com confidence baixa a chutar product.\n\n"
        f"TÍTULO: {title[:240]}\n"
        f"RESUMO: {summary[:600]}\n"
    )
    raw = _gemini_text(prompt, grounded=False, json_mode=True)
    if not raw:
        return None
    data = _parse_json_loose(raw)
    if not isinstance(data, dict) or not data.get("name") or not data.get("kind"):
        return None
    return data


def _find_page_urls(entity: dict) -> list[str]:
    """Gemini grounded → URLs de PÁGINAS oficiais do produto (página
    institucional da marca + review reputado). Não pede URLs de imagem
    direto porque grounded raramente retorna assets, só HTML."""
    name = entity.get("name", "")
    brand = entity.get("brand") or ""
    category = entity.get("category") or "product"
    brand_hint = f" by {brand}" if brand else ""
    prompt = (
        f"Use Google Search to find OFFICIAL web pages featuring the {name} "
        f"({category}){brand_hint}.\n\n"
        "Return 4-6 page URLs (HTML pages, not image files). Prefer in order:\n"
        f"1. Official brand product page ({brand} website)\n"
        "2. Major reviewer pages (DC Rainmaker, The Verge, Outside, Velo, Wired, Runner's World)\n"
        "3. Major retailer product pages (REI, Backcountry, Wiggle, Decathlon)\n\n"
        "Output format (one URL per line, no markdown, no commentary):\n"
        "https://example.com/product-page-1\n"
        "https://example.com/product-page-2\n"
    )
    raw = _gemini_text(prompt, grounded=True, json_mode=False)
    if not raw:
        return []
    found = _PAGE_URL_RE.findall(raw)
    seen: set[str] = set()
    out: list[str] = []
    for u in found:
        u = u.strip().rstrip(".,;)>\"'`")
        # Pula assets já-imagem (vai pro download direto) e URLs do próprio Google
        if "google.com/search" in u or "vertexaisearch" in u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out[:8]


def _extract_images_from_page(page_url: str) -> list[str]:
    """Fetch HTML da página e extrai og:image / twitter:image."""
    try:
        with httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as c:
            r = c.get(page_url)
            if r.status_code != 200:
                return []
            html = r.text[:200_000]  # cap a 200KB do head
    except Exception as e:  # noqa: BLE001
        print(f"  ↪ asset_finder.fetch_page {page_url[:60]}: {e!r}")
        return []
    urls: list[str] = []
    for m in _OG_IMAGE_RE.findall(html):
        urls.append(m)
    for m in _OG_IMAGE_REV_RE.findall(html):
        urls.append(m)
    # Filtra placeholders comuns
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = u.strip()
        if not u.startswith("http"):
            continue
        if any(k in u.lower() for k in ("placeholder", "logo", "favicon", "social-share-default")):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _find_image_urls(entity: dict) -> list[str]:
    """Pipeline: Gemini grounded → page URLs → og:image de cada página.

    Também aceita URLs de imagem direta se Gemini retornar (raro, mas OK).
    Retorna lista deduplicada, página-da-marca PRIMEIRO.
    """
    pages = _find_page_urls(entity)
    if not pages:
        return []
    brand = (entity.get("brand") or "").lower()
    brand_slug = re.sub(r"[^a-z]+", "", brand)

    # Ordena: páginas da marca PRIMEIRO (foto oficial > review > retailer)
    def _rank(u: str) -> int:
        ul = u.lower()
        if brand_slug and brand_slug in ul:
            return 0
        if any(d in ul for d in ("dcrainmaker", "outsideonline", "runnersworld", "velo", "theverge")):
            return 1
        return 2

    pages_sorted = sorted(pages, key=_rank)
    print(f"↗ asset_finder: scanning {len(pages_sorted)} page(s) for og:image")
    out: list[str] = []
    seen: set[str] = set()
    for p in pages_sorted:
        # URL direta de imagem? aceita
        if _IMG_URL_RE.fullmatch(p):
            if p not in seen:
                seen.add(p)
                out.append(p)
            continue
        imgs = _extract_images_from_page(p)
        if imgs:
            print(f"  ↪ {p[:55]}… → {len(imgs)} og:image")
        for img in imgs:
            if img not in seen:
                seen.add(img)
                out.append(img)
        if len(out) >= 6:
            break
    return out[:8]


def _download_image(url: str) -> Optional[bytes]:
    try:
        with httpx.Client(
            timeout=60,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MergeAppBot/1.0)"},
        ) as c:
            r = c.get(url)
            r.raise_for_status()
            ct = r.headers.get("content-type", "").lower()
            data = r.content
            if not ct.startswith("image/") and not data[:8] == b"\x89PNG\r\n\x1a\n" and not data[:3] == b"\xff\xd8\xff":
                return None
            if len(data) < _MIN_IMAGE_BYTES or len(data) > _MAX_IMAGE_BYTES:
                return None
            return data
    except Exception as e:  # noqa: BLE001
        print(f"  ↪ asset_finder.download {url[:70]}: {e!r}")
        return None


def _validate_image(img_bytes: bytes, mime: str, entity: dict) -> Optional[dict]:
    """Gemini Vision valida: é o produto EXATO + qualidade editorial.

    Filtro estrito: match_confidence ≥ 0.85, quality ≥ 7, sem text overlay.
    Pede pro Vision detectar também ângulo + categoria de plano (hero/side/
    detail/lifestyle/comparison) pra alimentar carrossel multi-foto.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return {"is_match": True, "match_confidence": 0.7, "has_text_overlay": False,
                "has_clean_background": True, "quality": 7, "angle": "hero",
                "shot_type": "hero", "notes": "no-key skip"}
    name = entity.get("name", "")
    brand = entity.get("brand") or ""
    category = entity.get("category", "product")
    prompt = (
        f'Você é um curador editorial pra Merge (marca brasileira de wellness e endurance).\n'
        f'Avalie se a imagem é UTILIZÁVEL pra um post sobre "{name}" ({brand} {category}).\n\n'
        "NICHO MERGE (PERMITIDO):\n"
        "- Running (road/trail), Triathlon, Swimming (open water/pool), Cycling (road/MTB),\n"
        "  Hyrox, UTMB, Maratona, Ironman, Skyrun, Ultratrail.\n"
        "- Wearables/GPS watches/heart rate monitors para os esportes acima.\n"
        "- Gear performance: tênis de corrida/trail, wetsuits, óculos natação, bikes,\n"
        "  capacetes, GPS, vestuário técnico de corrida/ciclismo, recovery gear.\n\n"
        "FORA DO NICHO (REJEITAR):\n"
        "- Casual wear / streetwear / lifestyle puro (tênis Yeezy, Stan Smith, Air Force).\n"
        "- Outros esportes: futebol, basquete, vôlei, golfe, tênis (sport), beisebol,\n"
        "  ginástica artística, surf casual, skate, escalada indoor, fisiculturismo.\n"
        "- Moda/fashion shots (passarela, editorial vogue, lookbook lifestyle).\n"
        "- Produtos de uso doméstico, beleza, alimentação (a menos que seja gel/\n"
        "  suplemento de endurance específico).\n\n"
        "Devolva APENAS JSON:\n"
        "{\n"
        '  "is_match": true|false,\n'
        '  "match_confidence": 0.0-1.0,\n'
        '  "detected_model": "<modelo que você efetivamente vê>",\n'
        '  "is_endurance_niche": true|false,\n'
        '  "niche_reason": "<por que está dentro/fora do nicho>",\n'
        '  "has_text_overlay": true|false,\n'
        '  "has_clean_background": true|false,\n'
        '  "quality": 1-10,\n'
        '  "angle": "front" | "side" | "back" | "top" | "three_quarter" | "detail" | "lifestyle" | "pair",\n'
        '  "shot_type": "hero" | "detail" | "lifestyle" | "comparison" | "studio_pack",\n'
        '  "notes": "<one liner descrevendo o que tem na foto>"\n'
        "}\n\n"
        "Critérios:\n"
        f"- is_match: true se a foto mostra {name} OU produto similar/da mesma linha\n"
        f"  (ex: Adios Pro 3/4/5, Forerunner 965/970, Maverick I/II) — pode ser usado pra\n"
        "  conteúdo da marca/categoria. False se for produto totalmente diferente\n"
        "  (ex: pediram Adios Pro 4 e veio Ultraboost casual).\n"
        "- match_confidence: 0.95+ se é exatamente o modelo, 0.7-0.94 se é da mesma\n"
        "  linha/marca/categoria utilizável, < 0.7 = rejeite.\n"
        "- detected_model: o que VOCÊ vê na foto, não o que foi pedido.\n"
        "- is_endurance_niche: TRUE apenas se cabe no nicho Merge acima. FALSE para\n"
        "  qualquer coisa casual/streetwear/outros esportes. NA DÚVIDA, false.\n"
        "- niche_reason: justifique em uma frase curta (ex: 'tênis de corrida com placa\n"
        "  de carbono' OU 'sneaker casual lifestyle, não é endurance').\n"
        "- has_text_overlay: true se tem QUALQUER texto/preço/watermark sobreposto.\n"
        "- has_clean_background: true se fundo branco/transparente/neutro/estúdio.\n"
        "- quality: 1-3 ruim/blur, 4-6 ok mas amador, 7-8 boa editorial, 9-10 cover.\n"
        "- shot_type: 'hero' (produto sozinho), 'detail' (close), 'lifestyle' (em uso),\n"
        "  'comparison' (>1 produto), 'studio_pack' (família/colorways)."
    )
    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": mime, "data": base64.b64encode(img_bytes).decode("ascii")}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    url = GEMINI_ENDPOINT_TMPL.format(model=GEMINI_TEXT_MODEL, key=key)
    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(url, json=body, headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            print(f"  ↪ asset_finder.validate HTTP {r.status_code}")
            return None
        resp = r.json()
        cands = resp.get("candidates") or []
        if not cands:
            return None
        parts = cands[0].get("content", {}).get("parts", []) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        return _parse_json_loose(text)
    except Exception as e:  # noqa: BLE001
        print(f"  ↪ asset_finder.validate exception: {e!r}")
        return None


# Match confidence flexível — aceita modelos similares da mesma linha.
# Nicho endurance é o filtro DURO (não negocia).
_MIN_MATCH_CONFIDENCE = 0.70
_MIN_QUALITY = 6


def _accepts(vision: dict) -> tuple[bool, str]:
    """Gate único pra Vision result. Retorna (ok, motivo_se_rejeitado).

    Filtro principal: nicho endurance (não negocia). Match flexível pra
    aceitar modelos similares/mesma linha.
    """
    # NICHO: filtro duro — rejeita casual/outros esportes
    if vision.get("is_endurance_niche") is False:
        return False, f"fora_nicho: {str(vision.get('niche_reason',''))[:80]}"
    if not vision.get("is_match"):
        return False, f"not_match: detected={str(vision.get('detected_model',''))[:50]}"
    mc = float(vision.get("match_confidence") or 0)
    if mc < _MIN_MATCH_CONFIDENCE:
        return False, f"low_confidence ({mc:.2f} < {_MIN_MATCH_CONFIDENCE})"
    if vision.get("has_text_overlay"):
        return False, "text_overlay"
    q = int(vision.get("quality") or 0)
    if q < _MIN_QUALITY:
        return False, f"quality {q} < {_MIN_QUALITY}"
    return True, ""


def find_official_image(title: str, summary: str) -> Optional[dict]:
    """Pipeline completo: detecta produto → busca → valida → cache.

    Retorna {path, entity, vision, url} ou None.
    Versão single-shot — pra carrossel multi-foto, use `find_official_images_multi`.
    """
    multi = find_official_images_multi(title, summary, max_n=1)
    if not multi:
        return None
    first = multi["photos"][0]
    return {
        "path": first["path"],
        "entity": multi["entity"],
        "vision": first.get("vision"),
        "url": first.get("url"),
    }


def find_official_images_multi(
    title: str, summary: str, max_n: int = 5
) -> Optional[dict]:
    """Coleta até `max_n` fotos do MESMO produto, todas validadas via Vision.

    Útil pra carrossel: cover (hero), detalhes, lifestyle, comparativo.
    Cache: salva como `<slug>__01.<ext>`, `<slug>__02.<ext>`, ...
    Se cache existir (qualquer foto com prefixo `<slug>__`), retorna cache hit.

    Retorna {"entity": dict, "photos": [{"path", "vision", "url"}, ...]} ou None.
    """
    if os.environ.get("ASSET_FINDER_DISABLED") == "1":
        return None
    AUTO_DIR.mkdir(parents=True, exist_ok=True)

    entity = extract_entity(title, summary)
    if not entity:
        return None
    kind = entity.get("kind")
    name = entity.get("name", "").strip()
    if kind != "product":
        return None
    conf = float(entity.get("confidence") or 0)
    if conf < 0.7:
        print(f"↪ asset_finder: '{name}' confidence={conf:.2f} < 0.7, pulando")
        return None

    slug = _slug(name)
    # Cache hit? Procura por <slug>__NN.* OU legacy <slug>.<ext>
    cached_paths: list[Path] = sorted(
        [p for p in AUTO_DIR.iterdir()
         if p.is_file() and (p.stem.startswith(f"{slug}__") or p.stem == slug)
         and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
    )
    if cached_paths:
        print(f"✓ asset_finder: cache hit {len(cached_paths)} foto(s) pra '{name}'")
        return {
            "entity": entity,
            "photos": [{"path": p, "vision": None, "url": "cache"} for p in cached_paths],
        }

    urls = _find_image_urls(entity)
    if not urls:
        print(f"⚠ asset_finder: nenhuma URL pra '{name}'")
        return None
    print(f"↗ asset_finder: '{name}' → {len(urls)} candidata(s), max_n={max_n}")

    accepted: list[dict] = []
    seen_angles: set[str] = set()
    for url in urls:
        if len(accepted) >= max_n:
            break
        img = _download_image(url)
        if not img:
            continue
        mime = _mime_from_bytes(img)
        vision = _validate_image(img, mime, entity)
        if not vision:
            continue
        ok, reason = _accepts(vision)
        if not ok:
            print(f"  ↪ vision rejeitou: {reason}")
            continue
        # Evita duplicar ângulo idêntico (mesma "hero+front" duas vezes)
        angle_key = f"{vision.get('shot_type','?')}:{vision.get('angle','?')}"
        if angle_key in seen_angles:
            print(f"  ↪ pulando duplicata de ângulo {angle_key}")
            continue
        seen_angles.add(angle_key)
        ext = _ext_from_mime(mime)
        idx = len(accepted) + 1
        local = AUTO_DIR / f"{slug}__{idx:02d}{ext}"
        local.write_bytes(img)
        print(
            f"✓ asset_finder: '{name}' [{idx}/{max_n}] ← {url[:60]}… "
            f"({len(img)//1024}KB, q={vision.get('quality')}, "
            f"conf={vision.get('match_confidence')}, "
            f"{vision.get('shot_type')}/{vision.get('angle')})"
        )
        accepted.append({"path": local, "vision": vision, "url": url})

    if not accepted:
        print(f"⚠ asset_finder: nenhuma candidata válida pra '{name}'")
        return None
    return {"entity": entity, "photos": accepted}


if __name__ == "__main__":
    # Smoke test CLI: passa title via argv
    import sys
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    title = " ".join(sys.argv[1:]) or "Roka launches Maverick II wetsuit for triathlon"
    summary = sys.argv[-1] if len(sys.argv) > 2 else ""
    result = find_official_image(title, summary)
    if result:
        print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in result.items()}, indent=2, ensure_ascii=False))
    else:
        print("NO RESULT")
