"""Resolução inteligente de bg pra news — multi-tier com quality gate.

Estratégia (acertar na primeira geração):

  1. Claude Sonnet classifica em ENTITY ou SCENE com prompt fotográfico forte.
     - ENTITY: nome próprio com foto canônica (atleta, evento, produto).
     - SCENE: cena editorial que IA renderiza bem.

  2. ENTITY → Wikipedia REST (pt → en) busca summary.originalimage.
     - Filtra logos/SVG/imagens pequenas. Devolve só foto editorial.

  3. SCENE → engine primário com fallback:
     - Se `GEMINI_API_KEY` setado: Gemini 2.5 Flash Image (paid, ~$0.04/img)
       como ABSOLUTE PRIMARY com retry 3x (backoff 2s, 5s) em 429/5xx.
       Pollinations FLUX só como safety net final se Gemini esgotar retries.
     - Sem key: Pollinations FLUX (free, sem fallback adicional).
     - Prompts photographer-grade obrigatórios (8 camadas: subject, action,
       setting, time, lighting, camera, lens, composition, grading).

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

REGRA 2 — scene_prompt (CRÍTICO — qualidade de capa de revista, 1ª geração):
SEMPRE em inglês, formato photographer-grade estrito. Estrutura obrigatória
com TODAS as 8 camadas (em ordem):

  "Editorial sports photograph for magazine cover. Subject: [SUBJECT —
  ethnicity/gender/age/build/expression/apparel/gear, MUITO específico].
  Action: [ACTION — verbo no gerúndio + detalhe corporal: gait, posture,
  contact phase, hand position]. Setting: [SETTING geográfico real +
  surface + atmospheric detail: heat haze, dust, mist, rain, sweat
  droplets]. Time of day: [golden hour 17:30 / blue hour 06:00 / harsh
  midday / overcast diffuse]. Lighting: [direction + quality + ratio —
  ex: 'warm rim light from camera-right at 45°, key light front-fill,
  dramatic key-to-fill ratio 4:1', or 'soft overcast diffuse omnidirectional
  with cool 5500K tone']. Camera: [Sony A7R V / Canon EOS R5 / Leica SL3 /
  Nikon Z9]. Lens: [exact focal + aperture — '85mm f/1.4 GM' / '70-200mm
  f/2.8 GM at 135mm' / '35mm f/1.4 wide environmental']. Settings: [shutter
  + ISO — '1/1250s ISO 400 frozen motion' or '1/60s ISO 100 motion blur on
  wheels']. Composition: [rule of thirds, subject on right-third
  intersection, leading lines from road/horizon, eye-level/low-angle].
  Color grading: [Kodak Portra 400 warm editorial / teal-and-orange
  cinematic / desaturated muted neutral / Fujifilm Eterna film stock].
  Aesthetic: photorealistic, sharp focus on eyes/subject, shallow depth
  of field with creamy bokeh background, magazine cover composition,
  subject in middle vertical third, generous negative space top and
  bottom, no text, no logos, no watermarks, no AI artifacts, no plastic
  skin, no over-saturation."

Exemplos GOLD STANDARD:

- "Editorial sports photograph for magazine cover. Subject: Brazilian
  male marathon runner, mid-30s, lean wiry build, technical singlet
  and short shorts, race bib mid-torso, sweat-soaked hair, jaw clenched
  in deep effort, gaze locked forward. Action: mid-stride at km 35,
  right foot in toe-off phase, left arm driving back. Setting: urban
  asphalt avenue with blurred crowd and Porto Alegre Guaíba waterfront
  in distant background, heat haze rising from pavement. Time of day:
  late afternoon golden hour 17:40. Lighting: warm rim light from
  camera-right at 30°, key-to-fill ratio 3:1, golden glow on shoulders
  and arms. Camera: Sony A7R V. Lens: 200mm f/2 GM. Settings: 1/2000s
  ISO 320 frozen mid-stride. Composition: rule of thirds, runner on
  right-third intersection, road as leading line from bottom-left,
  low-angle 30cm above asphalt. Color grading: Kodak Portra 400 warm
  editorial with rich skin tones. Aesthetic: photorealistic, tack-sharp
  eyes, shallow depth of field with creamy bokeh crowd, magazine cover
  composition, subject in middle vertical third, generous negative space
  top and bottom, no text, no logos, no AI artifacts, no plastic skin."

- "Editorial sports photograph for magazine cover. Subject: professional
  male cyclist, late-20s, athletic build, full team kit in dark navy and
  white, aero helmet with visor, tinted sunglasses, calm focused
  expression. Action: aerodynamic time-trial tuck position, hands on
  extension bars, back perfectly flat, cadence 95 RPM. Setting: empty
  Atlantic coastal road with rocky cliffs and ocean horizon background,
  fine sea spray in air. Time of day: sunrise blue-to-gold transition
  06:30. Lighting: backlit with sun rising behind subject creating warm
  halo on helmet edge, fill from ocean reflection. Camera: Canon EOS R5.
  Lens: 70-200mm f/2.8 L IS at 135mm. Settings: 1/200s ISO 200 motion
  blur on wheels. Composition: rule of thirds, cyclist centered on
  middle-right, road sweeping in from bottom-right as leading line, low
  panning angle. Color grading: teal-and-orange cinematic with crushed
  blacks. Aesthetic: photorealistic, razor-sharp on helmet/face, shallow
  depth of field, magazine cover composition, generous negative space
  top and bottom, no text, no logos, no AI artifacts."

ERROS QUE INVALIDAM o scene_prompt (rejeição automática):
- Abstrato ("concept of resilience", "feeling of speed") — VOID
- Subject vago ("a runner", "an athlete", "sport scene") — VOID
- Sem lente específica ou aperture — VOID
- Sem direção/qualidade de luz — VOID
- Sem hora do dia explícita — VOID
- Pedir TEXTO/LOGO na imagem ("with the words...", "title saying...") — VOID
- Etnia genérica quando há contexto BR (deve ser "Brazilian") — VOID
- Cores chapadas como única descrição ("dark blue background") — VOID

REGRA 3 — BR context (CRÍTICO pra autenticidade):
Se a notícia menciona atleta BR, prova BR, marca BR (Olympikus, Track&Field,
Centauro, Maratona do Rio, POA, SP, Asics Brasil, Mizuno BR), você DEVE:
1. Incluir "Brazilian" explicitamente no SUBJECT do scene_prompt (ex:
   "Brazilian male marathon runner", "Brazilian female cyclist")
2. Inserir BACKDROP geográfico específico, NÃO genérico:
   - Maratona POA / Porto Alegre → "Porto Alegre Guaíba waterfront with
     Usina do Gasômetro silhouette in background, late afternoon golden hour"
   - Maratona Rio → "Rio de Janeiro Copacabana coastline with Sugarloaf
     mountain visible in background, sunrise light"
   - Maratona SP → "São Paulo Avenida Paulista skyline at dawn, city
     buildings in background"
   - Trail BR (Sertões, Eco) → "Brazilian Cerrado red dirt trail with
     dry vegetation, dramatic light"
3. Marcar br_context=true

REGRA 4 — assertividade ASSUNTO/PRODUTO/EVENTO:
A imagem PRECISA conversar com o tema EXATO da notícia. Não é genérica.
- Se notícia é sobre um PRODUTO específico (ex: "Garmin Forerunner 970",
  "Nike Vaporfly 4", "Apple Watch Ultra 3"): strategy=entity, busca foto
  oficial. Se Wikipedia não tem, scene com nome do produto literal:
  "Editorial photograph of athlete wearing Garmin Forerunner 970 close-up
  on wrist, training context".
- Se notícia é sobre um EVENTO específico (ex: "Maratona Olympikus de
  Porto Alegre 2026", "Cocodona 250"): scene com indicações geográficas
  e visuais específicas DAQUELE evento (terreno, clima, urbano vs trail,
  época do ano).
- Se notícia é sobre uma TÉCNICA/MÉTODO (ex: "taper", "zona 2",
  "pliometria"): scene com atleta EXECUTANDO a técnica de forma
  reconhecível visualmente (taper = corredor em pace fácil, leve;
  zona 2 = trote sustentado relaxado; pliometria = saltos explosivos).
- Se notícia é sobre RESULTADO ESPORTIVO: scene de atleta na ação que
  define o resultado (chegada vitoriosa, sprint, climbing, podium).

REGRA 5 — entity:
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


_GEMINI_PROMPT_SUFFIX = (
    "\n\n--- TECHNICAL SPECS ---\n"
    "Aspect ratio: 9:16 portrait (1080x1920). "
    "Subject MUST sit in the middle vertical third of the frame, with "
    "generous negative space in the top third AND bottom third (will be "
    "cropped to 4:5 for feed). "
    "Render quality: 8K resolution, photorealistic, ultra-detailed skin "
    "texture and fabric weave, sharp focus on eyes, natural film grain, "
    "shot like a Sports Illustrated / Outside Magazine cover.\n\n"
    "--- HARD NEGATIVES (do NOT include) ---\n"
    "no text, no captions, no headlines, no logos, no watermarks, no "
    "brand marks, no signage with words, no AI artifacts, no plastic "
    "skin, no waxy faces, no over-saturation, no HDR halos, no fused "
    "fingers, no extra limbs, no mannequin look, no stock-photo cliché, "
    "no centered front-facing pose unless specified, no cartoon, no "
    "illustration, no 3D render, no CGI."
)


def _fetch_gemini_image(prompt: str) -> Optional[bytes]:
    """Gera via Gemini 2.5 Flash Image (PRIMARY engine). Retry em 5xx/429.

    Formato 9:16 (1080x1920) — mesma imagem serve feed (4:5 cropa topo/rodapé
    via CSS background-size:cover) e story (9:16 nativo). Economiza 50%
    de custo (1 chamada para 2 formatos).

    Retry: até 3 tentativas com backoff (2s, 5s) em 429/5xx. Nunca tenta
    de novo em 4xx que não 429 (config error não vai melhorar).
    """
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    url = GEMINI_ENDPOINT_TMPL.format(model=GEMINI_MODEL, key=key)
    full_prompt = prompt + _GEMINI_PROMPT_SUFFIX
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    backoffs = [2, 5]  # 3 tentativas: imediata, +2s, +5s
    last_status: Optional[int] = None
    for attempt in range(3):
        if attempt > 0:
            time.sleep(backoffs[attempt - 1])
        try:
            with httpx.Client(timeout=120) as client:
                r = client.post(url, json=body, headers={"Content-Type": "application/json"})
                last_status = r.status_code
                if r.status_code == 200:
                    resp = r.json()
                    for cand in resp.get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            inline = part.get("inlineData") or part.get("inline_data")
                            if inline and inline.get("data"):
                                img = base64.b64decode(inline["data"])
                                print(
                                    f"✓ visual: gemini {GEMINI_MODEL} → "
                                    f"{len(img)//1024}KB (attempt {attempt+1})"
                                )
                                return img
                    print("⚠ visual.gemini: resposta 200 sem imagem")
                    return None  # 200 sem imagem = problema de prompt, não retry
                # Retry só em 429 (rate limit) e 5xx (server)
                if r.status_code == 429 or r.status_code >= 500:
                    snippet = r.text[:200]
                    print(
                        f"↪ visual.gemini HTTP {r.status_code} "
                        f"(attempt {attempt+1}/3): {snippet}"
                    )
                    continue
                # 4xx que não 429 = config/auth/quota — não adianta retry
                snippet = r.text[:200]
                print(f"⚠ visual.gemini HTTP {r.status_code} (no retry): {snippet}")
                return None
        except Exception as e:  # noqa: BLE001
            print(f"↪ visual.gemini exception (attempt {attempt+1}/3): {e!r}")
            continue
    print(f"⚠ visual.gemini esgotou retries (last={last_status})")
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
        # Fallback puro (classifier offline): prompt photographer-grade genérico
        plan = {
            "strategy": "scene",
            "scene_prompt": (
                f"Editorial sports photograph for magazine cover. "
                f"Subject: professional {modality or 'endurance'} athlete, "
                f"athletic build, technical apparel, intense focused expression. "
                f"Action: peak effort moment in their discipline. "
                f"Setting: real outdoor environment matching the sport. "
                f"Time of day: late afternoon golden hour 17:30. "
                f"Lighting: warm rim light from camera-right at 30°, "
                f"key-to-fill ratio 3:1, dramatic golden glow. "
                f"Camera: Sony A7R V. Lens: 85mm f/1.4 GM. "
                f"Settings: 1/1250s ISO 400. "
                f"Composition: rule of thirds, subject on right-third "
                f"intersection, low-angle perspective. "
                f"Color grading: Kodak Portra 400 warm editorial. "
                f"Aesthetic: photorealistic, tack-sharp eyes, shallow depth "
                f"of field with creamy bokeh, magazine cover composition, "
                f"subject in middle vertical third, generous negative space "
                f"top and bottom, no text, no logos, no AI artifacts, "
                f"no plastic skin."
            ),
        }

    img_bytes: Optional[bytes] = None
    source_label = ""
    has_gemini = bool(os.environ.get("GEMINI_API_KEY"))

    def _try_scene(prompt: str, label_prefix: str) -> tuple[Optional[bytes], str]:
        """Gemini ABSOLUTE PRIMARY (paid, top quality, com retry interno).
        FLUX só como safety net se Gemini falhar TODAS as tentativas.
        Quality gate em cada engine.
        """
        if has_gemini:
            b = _fetch_gemini_image(prompt)
            if _passes_quality_gate(b):
                return b, f"{label_prefix}gemini"
            print("↪ visual: gemini esgotado, safety-net FLUX")
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
                f"Editorial sports photograph for magazine cover. "
                f"Subject: {plan['entity']} in their signature "
                f"{modality or 'endurance'} discipline, professional athlete, "
                f"intense focused expression, technical apparel and gear. "
                f"Action: peak performance moment, body in dynamic motion. "
                f"Setting: real environment iconic to this athlete/event. "
                f"Time of day: late afternoon golden hour 17:30. "
                f"Lighting: warm rim light from camera-right at 30°, "
                f"key-to-fill ratio 3:1. "
                f"Camera: Sony A7R V. Lens: 200mm f/2 GM. "
                f"Settings: 1/2000s ISO 400 frozen motion. "
                f"Composition: rule of thirds, low-angle hero shot. "
                f"Color grading: Kodak Portra 400 warm editorial. "
                f"Aesthetic: photorealistic, tack-sharp eyes, shallow depth "
                f"of field, magazine cover composition, subject in middle "
                f"vertical third, generous negative space top and bottom, "
                f"no text, no logos, no AI artifacts, no plastic skin."
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

    try:
        from news.visual_report import log_generation
        log_generation(aid=aid, source_label=source_label, byte_size=len(img_bytes))
    except Exception as e:  # noqa: BLE001
        print(f"⚠ visual.log_generation falhou: {e!r}")

    return _upload_r2(local, f"{R2_PREFIX}/{aid}{ext}")
