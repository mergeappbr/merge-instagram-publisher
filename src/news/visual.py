"""Resolução inteligente de bg pra news — multi-tier com quality gate.

Estratégia (acertar na primeira geração):

  1. Claude Sonnet classifica em ENTITY ou SCENE com prompt fotográfico forte.
     - ENTITY: nome próprio com foto canônica (atleta, evento, produto).
     - SCENE: cena editorial que IA renderiza bem.

  2. ENTITY → Wikipedia REST (pt → en) busca summary.originalimage.
     - Filtra logos/SVG/imagens pequenas. Devolve só foto editorial.

  3. SCENE → engine primário com fallback:
     - Se `GEMINI_API_KEY` setado: Gemini 2.5 Flash Image (paid, ~$0.04/img)
       como primary; Pollinations FLUX como fallback se Gemini falhar.
     - Sem key: Pollinations FLUX (free, sem fallback adicional).

  4. Quality gate: imagem precisa ter ≥50KB e magic bytes válidos.
     Imagens muito pequenas geralmente são erro/abstract/text-only.

  5. Download → upload R2 → URL pública. Sobrevive redeploy Railway.

Falha silenciosa: devolve None se TODOS os caminhos falharem; caller
mantém bg que o writer escolheu do banco.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx

GEMINI_MODEL = "gemini-2.5-flash-image"
GEMINI_ENDPOINT_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)
MIN_IMAGE_BYTES = 50 * 1024  # <50KB → provavelmente erro/abstract

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = ROOT / "output" / "feed" / "_news_bg"
R2_PREFIX = "news_bg"

CLASSIFIER_SYS = """Você decide qual estratégia visual usa pra um post de news de endurance/wellness.

Devolva JSON com:
{
  "strategy": "entity" | "scene",
  "entity": "<nome canônico da entidade pra buscar na Wikipedia>" (só se strategy=entity),
  "scene_prompt": "<descrição fotográfica em inglês pra IA gerar foto>" (só se strategy=scene),
  "wiki_lang": "pt" | "en" (pt primeiro se entidade brasileira/lusófona),
  "br_context": true | false (se a notícia tem ângulo Brasil — atleta, prova, marca BR)
}

REGRA 1 — ENTITY vs SCENE:
- ENTITY: nome próprio CANÔNICO com Wikipedia provável: atleta famoso
  ("Eliud Kipchoge", "Tadej Pogačar", "Magdalena Boulet"), evento batizado
  ("Enhanced Games", "Ironman World Championship", "UTMB", "Maratona do Rio"),
  produto modelo específico ("Garmin Forerunner 965", "Apple Watch Ultra 2"),
  organização ("UCI", "World Athletics", "CBAt").
- SCENE: tudo mais — estudos, tendências, conceitos, debates, lançamentos
  sem nome próprio único forte ("estudo sobre cafeína em ultra", "novo
  estudo sobre HIIT", "polêmica regra ciclismo", "como evitar fadiga
  no km 35").

REGRA 2 — scene_prompt (CRÍTICO pra acertar na 1ª geração):
SEMPRE em inglês, formato fotográfico estrito:

  "Editorial photograph of [SUBJECT] [ACTION], [SETTING], [LIGHTING],
  [COMPOSITION], shot on [LENS], shallow depth of field, magazine cover
  composition, subject centered, negative space top and bottom, no text, no logos, photorealistic"

Exemplos do que QUERO:
- "Editorial photograph of a Brazilian marathon runner pushing through
  fatigue at km 35 on urban asphalt, late afternoon golden light,
  determined expression, side profile, shot on 85mm f/1.8, shallow
  depth of field, magazine cover composition, subject centered, negative space top and bottom,
  no text, no logos, photorealistic"
- "Editorial photograph of professional cyclist in aerodynamic time-trial
  position on coastal road, sunrise backlight, motion blur on wheels,
  shot on 70-200mm f/2.8, magazine cover composition, negative space at
  bottom, no text, no logos, photorealistic"

ERROS A EVITAR no scene_prompt:
- Abstrato/conceitual ("the concept of resilience") — VOID
- Sem subject específico ("sport scene") — VOID
- Sem lighting ("a runner") — VOID
- Cores sem contexto ("dark blue background") — VOID
- Pedir TEXTO na imagem (qualquer "with the words..." / "title saying...") — VOID

REGRA 3 — BR context:
Se a notícia menciona atleta BR, prova BR, marca BR (Olympikus, Track&Field,
Centauro, Maratona do Rio, POA, SP), inclua "Brazilian" no SUBJECT do
scene_prompt e marque br_context=true. Backdrop pode ser urbano BR
("São Paulo skyline", "Rio coastline", "Porto Alegre orla").

REGRA 4 — entity:
Forma exata da página Wikipedia. Para PT, forma português
("Eliud Kipchoge", "Maratona do Rio de Janeiro"). Para evento/marca em
inglês ("Enhanced Games", "UCI"), wiki_lang=en.
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


def _passes_quality_gate(img_bytes: Optional[bytes]) -> bool:
    """Tamanho mínimo + magic bytes válidos. Filtra erro/abstract/text-only."""
    if not img_bytes or len(img_bytes) < MIN_IMAGE_BYTES:
        return False
    head = img_bytes[:12]
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if head[:3] == b"\xff\xd8\xff":  # JPEG
        return True
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    return False


def _fetch_gemini_image(prompt: str) -> Optional[bytes]:
    """Gera via Gemini 2.5 Flash Image (paid). Retorna None silenciosamente em falha.

    Formato 9:16 (1080x1920) — mesma imagem serve feed (4:5 cropa topo/rodapé
    via CSS background-size:cover) e story (9:16 nativo). Economiza 50%
    de custo (1 chamada para 2 formatos).
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    url = GEMINI_ENDPOINT_TMPL.format(model=GEMINI_MODEL, key=key)
    full_prompt = (
        prompt
        + "\n\nAspect ratio: 9:16 portrait (1080x1920). "
        + "Subject MUST be centered vertically in the middle third of the frame. "
        + "Generous negative space at top AND bottom thirds (will be cropped to 4:5 "
        + "in feed format). No text, no logos, no watermarks anywhere."
    )
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    try:
        with httpx.Client(timeout=120) as client:
            r = client.post(url, json=body, headers={"Content-Type": "application/json"})
            if r.status_code != 200:
                snippet = r.text[:200]
                print(f"⚠ visual.gemini HTTP {r.status_code}: {snippet}")
                return None
            resp = r.json()
        for cand in resp.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    img = base64.b64decode(inline["data"])
                    print(f"✓ visual: gemini {GEMINI_MODEL} → {len(img)//1024}KB")
                    return img
        print("⚠ visual.gemini: resposta sem imagem")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual.gemini falhou: {e!r}")
        return None


def _fetch_pollinations(prompt: str) -> Optional[bytes]:
    """Gera via Pollinations FLUX (grátis, sem key).

    Formato 9:16 (1080x1920) — mesma imagem serve feed (cropada via CSS
    background-size:cover pra 4:5) e story (9:16 nativo).
    """
    enhanced_prompt = (
        prompt
        + " | Subject centered in middle third, generous negative space "
        + "top and bottom, 9:16 portrait, no text, no logos."
    )
    qs = urllib.parse.urlencode({
        "model": "flux",
        "width": 1080,
        "height": 1920,
        "nologo": "true",
        "enhance": "true",
        "seed": int(time.time()) % 100000,
    })
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(enhanced_prompt)}?{qs}"
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
                f"magazine cover composition with subject centered, negative space top and bottom"
            ),
        }

    img_bytes: Optional[bytes] = None
    source_label = ""
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))

    def _try_scene(prompt: str, label_prefix: str) -> tuple[Optional[bytes], str]:
        """Gemini → Pollinations, com quality gate em cada um."""
        if has_gemini:
            b = _fetch_gemini_image(prompt)
            if _passes_quality_gate(b):
                return b, f"{label_prefix}gemini"
            print("↪ visual: gemini falhou/qualidade ruim, fallback FLUX")
        b = _fetch_pollinations(prompt)
        if _passes_quality_gate(b):
            return b, f"{label_prefix}flux"
        return None, f"{label_prefix}none"

    if plan.get("strategy") == "entity" and plan.get("entity"):
        wiki_bytes = _fetch_wikipedia_image(
            plan["entity"], lang=plan.get("wiki_lang", "pt")
        )
        if _passes_quality_gate(wiki_bytes):
            img_bytes = wiki_bytes
            source_label = f"wiki:{plan['entity']}"
        else:
            # entity sem foto / quality gate falhou → scene gerada
            print(f"↪ visual: wiki '{plan['entity']}' sem foto válida, caindo pra scene")
            scene_prompt = plan.get("scene_prompt") or (
                f"Editorial photograph of {plan['entity']}, "
                f"{modality or 'endurance'} context, cinematic golden hour, "
                f"shallow depth of field, magazine cover composition, "
                f"subject centered, negative space top and bottom, no text, no logos, photorealistic"
            )
            img_bytes, source_label = _try_scene(
                scene_prompt, f"scene-fallback:{plan['entity']}:"
            )

    elif plan.get("scene_prompt"):
        img_bytes, source_label = _try_scene(plan["scene_prompt"], "scene:")

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
