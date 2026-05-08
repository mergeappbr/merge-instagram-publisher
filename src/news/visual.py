"""Resolução inteligente de bg pra news — gratuita e autônoma.

Caminhos (todos free, zero billing):

  1. Claude Sonnet classifica em ENTITY ou SCENE.
     - ENTITY: notícia gira em torno de nome próprio com foto canônica
       (Enhanced Games, UCI, Jan-Willem van Schip, Garmin Forerunner 965).
     - SCENE: notícia descreve cena genérica que IA renderiza bem
       (ciclista em ação aerodinâmica, corredor cruzando linha).

  2. ENTITY → Wikipedia REST (pt → en) busca summary.originalimage
     - api/rest_v1/page/summary/<title> devolve {originalimage:{source}}
     - Usa imagem de Wikimedia Commons (CC, livre).

  3. SCENE → Pollinations.ai FLUX (grátis, sem key, sem rate hard)
     - GET image.pollinations.ai/prompt/<encoded>?model=flux&width=1080&height=1350&nologo=true
     - Devolve PNG/JPEG dos bytes diretos.

  4. Download → upload R2 → URL pública. Sobrevive redeploy Railway.

Falha silenciosa: devolve None se nada funcionar, caller mantém bg do writer.
"""
from __future__ import annotations

import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = ROOT / "output" / "feed" / "_news_bg"
R2_PREFIX = "news_bg"

CLASSIFIER_SYS = """Você decide qual estratégia visual usa pra um post de news de endurance/wellness.

Devolva JSON com:
{
  "strategy": "entity" | "scene",
  "entity": "<nome canônico da entidade pra buscar na Wikipedia>" (só se strategy=entity),
  "scene_prompt": "<descrição editorial em inglês pra IA gerar foto>" (só se strategy=scene),
  "wiki_lang": "pt" | "en" (pt primeiro se entidade brasileira/lusófona)
}

Regras:
- ENTITY quando há nome próprio CANÔNICO (atleta famoso, evento batizado, produto modelo, organização) que tem página de Wikipedia provável. Ex: "Enhanced Games", "UCI", "Ironman Brasil", "Garmin Forerunner 965", "Eliud Kipchoge".
- SCENE quando notícia é sobre conceito/tendência/cena ampla. Ex: "tendência de cafeína em ultra", "novo estudo sobre HIIT", "polêmica regra ciclismo".
- scene_prompt: SEMPRE em inglês, formato editorial: SUBJECT + ACTION + LIGHTING + STYLE + COMPOSITION. Ex: "Professional cyclist in aerodynamic time-trial position on road, cinematic side angle, golden hour lighting, shallow depth of field, magazine editorial". MAX 200 chars. SEM TEXTO na imagem. Negative space na parte inferior pra overlay.
- entity: forma exata da página Wikipedia. Para PT, use a forma português ("Eliud Kipchoge"). Para evento/marca em inglês ("Enhanced Games"), pode ficar wiki_lang=en.
"""


def _classify(title: str, summary: str, modality: str) -> Optional[dict]:
    """Pergunta ao Claude qual estratégia usar."""
    try:
        from llm import complete_json
        user = (
            f"Title: {title}\n"
            f"Summary: {summary[:500]}\n"
            f"Modality: {modality}"
        )
        return complete_json(system=CLASSIFIER_SYS, user=user, fast=True, temperature=0.3)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual.classify falhou: {e!r}")
        return None


def _fetch_wikipedia_image(entity: str, lang: str = "pt") -> Optional[bytes]:
    """Busca originalimage do summary REST. Tenta lang pedido + fallback en."""
    headers = {
        "User-Agent": "MergeAppBot/1.0 (https://github.com/mergeappbr; merge@mergeapp.com.br)"
    }
    candidates = [lang, "en"] if lang != "en" else ["en", "pt"]
    for lg in dict.fromkeys(candidates):  # dedupe preservando ordem
        url = (
            f"https://{lg}.wikipedia.org/api/rest_v1/page/summary/"
            f"{urllib.parse.quote(entity.replace(' ', '_'))}"
        )
        try:
            with httpx.Client(timeout=30, follow_redirects=True, headers=headers) as client:
                r = client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json()
                orig = data.get("originalimage") or {}
                img_url = orig.get("source") or (data.get("thumbnail") or {}).get("source")
                if not img_url:
                    continue
                # Skip logos/SVGs/imagens muito pequenas — pra BG full-bleed
                # precisa ser foto editorial, não brand mark.
                lower = img_url.lower()
                if any(t in lower for t in ("logo", "wordmark", "_seal", ".svg")):
                    print(f"↪ visual.wiki[{lg}] '{entity}': pulando brand mark {img_url.split('/')[-1]}")
                    continue
                if orig.get("width", 0) and orig["width"] < 400:
                    print(f"↪ visual.wiki[{lg}] '{entity}': muito pequena ({orig.get('width')}px)")
                    continue
                ir = client.get(img_url)
                ir.raise_for_status()
                print(f"✓ visual: wiki[{lg}] '{entity}' → {len(ir.content)//1024}KB")
                return ir.content
        except Exception as e:  # noqa: BLE001
            print(f"⚠ visual.wiki[{lg}] '{entity}': {e!r}")
            continue
    return None


def _fetch_pollinations(prompt: str) -> Optional[bytes]:
    """Gera via Pollinations FLUX (grátis, sem key)."""
    qs = urllib.parse.urlencode({
        "model": "flux",
        "width": 1080,
        "height": 1350,
        "nologo": "true",
        "enhance": "true",
        "seed": int(time.time()) % 100000,
    })
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?{qs}"
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if not ct.startswith("image/"):
                print(f"⚠ visual.pollinations: content-type inesperado {ct}")
                return None
            print(f"✓ visual: pollinations FLUX → {len(r.content)//1024}KB")
            return r.content
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual.pollinations falhou: {e!r}")
        return None


def _upload_r2(local_path: Path, key: str) -> Optional[str]:
    """Sobe pro R2 público (CDN). Devolve URL absoluta ou file:// fallback."""
    base = os.environ.get("R2_PUBLIC_BASE_URL")
    if not base:
        return f"file://{local_path}"
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
        client = boto3.client(
            "s3",
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4"),
        )
        ctype = "image/png" if local_path.suffix.lower() == ".png" else "image/jpeg"
        client.upload_file(
            str(local_path),
            os.environ["R2_BUCKET"],
            key,
            ExtraArgs={"ContentType": ctype, "CacheControl": "public, max-age=31536000"},
        )
        return f"{base.rstrip('/')}/{key}"
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual.r2 falhou: {e!r}")
        return f"file://{local_path}"


def resolve_bg_for_news(
    aid: str, title: str, summary: str, modality: str
) -> Optional[str]:
    """Resolve BG_IMAGE pra um item de news. Devolve URL pública ou None."""
    if os.environ.get("NEWS_VISUAL_DISABLED") == "1":
        return None

    plan = _classify(title, summary, modality)
    if not plan:
        # Fallback puro: Pollinations com prompt simples
        plan = {
            "strategy": "scene",
            "scene_prompt": (
                f"Editorial photography of {modality or 'endurance athlete'} "
                f"in action, cinematic golden hour, shallow depth of field, "
                f"magazine cover composition with negative space at bottom"
            ),
        }

    img_bytes: Optional[bytes] = None
    source_label = ""

    if plan.get("strategy") == "entity" and plan.get("entity"):
        img_bytes = _fetch_wikipedia_image(
            plan["entity"], lang=plan.get("wiki_lang", "pt")
        )
        source_label = f"wiki:{plan['entity']}"
        if not img_bytes:
            # entity sem foto → cai pra scene gerada
            print(f"↪ visual: wiki sem foto pra '{plan['entity']}', caindo pra FLUX")
            scene_prompt = plan.get("scene_prompt") or (
                f"Editorial photography about {plan['entity']}, "
                f"{modality or 'endurance'} context, cinematic, magazine style"
            )
            img_bytes = _fetch_pollinations(scene_prompt)
            source_label = f"flux-fallback:{plan['entity']}"

    elif plan.get("scene_prompt"):
        img_bytes = _fetch_pollinations(plan["scene_prompt"])
        source_label = "flux"

    if not img_bytes:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Detecta extensão pelos magic bytes
    ext = ".jpg"
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        ext = ".png"
    elif img_bytes[:6] in (b"GIF87a", b"GIF89a"):
        ext = ".gif"
    elif img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        ext = ".webp"

    local = CACHE_DIR / f"{aid}{ext}"
    local.write_bytes(img_bytes)
    print(f"↗ visual: {aid} ← {source_label} ({local.name}, {len(img_bytes)//1024}KB)")

    return _upload_r2(local, f"{R2_PREFIX}/{aid}{ext}")
