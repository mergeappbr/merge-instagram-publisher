#!/usr/bin/env python3
"""
Geração de imagem pra creatives Merge — modo prompt (manual via Gemini web)
ou modo API (Gemini API, requer billing ativado).

Workflow padrão (modo prompt):
  1. python3 src/imgen.py prompt --slug X --preset PRODUCT --subject "..."
  2. Copia o prompt impresso, cola em https://gemini.google.com (logado @mergeapp.com.br)
  3. Baixa o PNG gerado
  4. Salva no path indicado pelo helper
  5. render.py automaticamente pega o arquivo (busca subdir 'ai/' primeiro)

Presets disponíveis (--preset):
  product       — produto isolado em fundo branco (wearable, calçado)
  bg_editorial  — BG cinematográfico moody (atleta em ação)
  bg_brazil     — cena brasileira (landmark + atleta)
  mockup        — interface fictícia (relógio/celular mostrando dados)

Modo API (futuro, quando billing ativo):
  python3 src/imgen.py api --slug X --prompt "..."
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "brand" / "images" / "_bank"
ENV = ROOT / ".env"

# Carrega .env (sem dep externa)
if ENV.exists():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

DEFAULT_MODEL = "gemini-2.5-flash-image"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)

# ---- Presets do design system Merge ----------------------------------------
# Cada preset tem: descrição, aspect default, template com placeholders.
# Placeholders comuns: {subject}, {detail}, {extra}.
# Defaults sobrescrevíveis via flags --location, --time-of-day, --neg-space etc.

PRESETS: dict[str, dict] = {
    "product": {
        "description": "Produto isolado em fundo branco (wearable, calçado, equipamento)",
        "aspect": "1:1",
        "template": (
            "Product photography of {subject}. {detail} "
            "{angle}. Pure white seamless backdrop, professional studio. "
            "Lighting: single softbox key light from upper-left, fill from right, "
            "soft contact shadow lower-right. Hyperrealistic, ultra-sharp focus on "
            "main subject, premium e-commerce campaign style. "
            "No text overlays, no watermarks, no logos beyond native branding on the product itself. "
            "Aspect ratio: {aspect}."
        ),
        "fields": {
            "subject": "obrigatório — ex: 'Garmin Forerunner 965 GPS smartwatch'",
            "detail": "opcional — ex: 'AMOLED display showing pace 5:30/km, black silicone band'",
            "angle": "default '3/4 angle view, slight tilt'",
        },
        "defaults": {
            "angle": "3/4 angle view with slight tilt",
            "detail": "",
        },
    },
    "bg_editorial": {
        "description": "BG cinematográfico, dark/moody — atleta em ação pra usar como fundo de creative",
        "aspect": "4:5",
        "template": (
            "Editorial sports photography of {subject}. {detail} "
            "Cinematic, moody, low-key lighting with a single rim light defining the silhouette. "
            "Deep shadows, high contrast, dominant black and deep navy palette with subtle warm highlights. "
            "Shot on 35mm, shallow depth of field, grain. "
            "Composition leaves negative space {neg_space} for typography overlay. "
            "{face_rule} No text, no watermarks. "
            "Aspect ratio: {aspect}."
        ),
        "fields": {
            "subject": "obrigatório — ex: 'a male trail runner cresting a ridge at dawn'",
            "detail": "opcional — ex: 'wearing dark windbreaker, breath visible in cold air'",
            "neg_space": "default 'in the upper-left and bottom thirds'",
        },
        "defaults": {
            "detail": "",
            "neg_space": "in the upper-left and bottom thirds of the frame",
            "face_rule": "Subject is shot from behind or 3/4 — no face directly to camera.",
        },
    },
    "bg_brazil": {
        "description": "Cena brasileira específica (landmark + atleta) com ar editorial",
        "aspect": "4:5",
        "template": (
            "Editorial photography of {subject} at {location}, {time_of_day}. {detail} "
            "Atmospheric, cinematic, photorealistic. Local Brazilian context visible "
            "(architecture, vegetation, atmosphere). Color palette: deep shadows with "
            "warm golden highlights. Shot on 35mm, shallow depth of field. "
            "Composition leaves negative space {neg_space} for text overlay. "
            "{face_rule} No text, no watermarks, no signage in clear focus. "
            "Aspect ratio: {aspect}."
        ),
        "fields": {
            "subject": "obrigatório — ex: 'a cyclist riding'",
            "location": "obrigatório — ex: 'Avenida Paulista, São Paulo'",
            "time_of_day": "default 'early morning golden hour'",
        },
        "defaults": {
            "time_of_day": "early morning golden hour",
            "detail": "",
            "neg_space": "in the upper-left third",
            "face_rule": "Subject from behind or in profile — no face directly to camera.",
        },
    },
    "mockup": {
        "description": "Mockup de interface (relógio/celular/app fictício)",
        "aspect": "1:1",
        "template": (
            "Product mockup of {device} displaying {interface}. "
            "Dark mode UI, clean modern flat design with a single subtle accent color. "
            "{detail} Floating on neutral dark surface, soft ambient light from above, "
            "slight reflection on the surface. Hyperrealistic render. "
            "No real brand logos beyond {brand_rule}. Screen content is plausible and legible. "
            "Aspect ratio: {aspect}."
        ),
        "fields": {
            "device": "obrigatório — ex: 'Garmin Forerunner watch'",
            "interface": "obrigatório — ex: 'a running activity summary screen with pace graph and HR zones'",
        },
        "defaults": {
            "detail": "",
            "brand_rule": "the device manufacturer's native branding",
        },
    },
}


# ---- Modo prompt (manual) --------------------------------------------------

def build_prompt(preset_name: str, fields: dict[str, str]) -> tuple[str, str]:
    """Retorna (prompt_completo, aspect_default) pro preset."""
    if preset_name not in PRESETS:
        sys.exit(
            f"Preset desconhecido: {preset_name}. "
            f"Disponíveis: {', '.join(PRESETS.keys())}"
        )
    p = PRESETS[preset_name]
    merged = {**p["defaults"], **{k: v for k, v in fields.items() if v}}
    merged["aspect"] = fields.get("aspect") or p["aspect"]

    try:
        prompt = p["template"].format(**merged)
    except KeyError as e:
        sys.exit(f"Campo obrigatório faltando no preset {preset_name}: {e}")

    # Limpa duplos espaços
    prompt = " ".join(prompt.split())
    return prompt, merged["aspect"]


def cmd_prompt(args: argparse.Namespace) -> None:
    fields = {
        "subject": args.subject or "",
        "detail": args.detail or "",
        "location": args.location or "",
        "time_of_day": args.time_of_day or "",
        "angle": args.angle or "",
        "neg_space": args.neg_space or "",
        "device": args.device or "",
        "interface": args.interface or "",
        "aspect": args.aspect_ratio or "",
    }
    prompt, aspect = build_prompt(args.preset, fields)

    out_dir = BANK / args.slug / "ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("*.png"))) + 1
    out_path = out_dir / f"{n}.png"

    # Registra a intenção no _meta.json (pra rastreabilidade)
    meta_path = out_dir / "_meta.json"
    meta: dict = {"entries": []}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    meta.setdefault("entries", []).append(
        {
            "file": out_path.name,
            "mode": "manual",
            "preset": args.preset,
            "model": "gemini (web app, manual)",
            "prompt": prompt,
            "aspect": aspect,
            "fields": {k: v for k, v in fields.items() if v},
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "saved": False,
        }
    )
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Output
    bar = "═" * 70
    print(f"\n{bar}")
    print(f"  PROMPT — preset {args.preset} · slug '{args.slug}'")
    print(f"{bar}\n")
    print(prompt)
    print(f"\n{bar}")
    print(f"  PASSO A PASSO")
    print(f"{bar}")
    print(f"  1. Copia o prompt acima")
    print(f"  2. Cola em https://gemini.google.com (logado @mergeapp.com.br)")
    print(f"  3. Baixa o PNG")
    print(f"  4. Salva como:  {out_path.relative_to(ROOT)}")
    print(f"  5. Me avisa que está salvo — eu re-renderizo o post\n")
    print(f"  Aspect ratio sugerida: {aspect}")
    print(f"  Meta registrada em: {meta_path.relative_to(ROOT)}\n")


# ---- Modo API (Gemini, requer billing) -------------------------------------

def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY ausente em .env.")
    return key


def generate_via_api(
    prompt: str,
    *,
    slug: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str | None = None,
    retries: int = 2,
) -> Path:
    """Chama Gemini API e salva imagem. Requer billing ativado."""
    key = _api_key()
    url = GEMINI_ENDPOINT.format(model=model, key=key)

    full_prompt = prompt + (f"\n\nAspect ratio: {aspect_ratio}." if aspect_ratio else "")
    body = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    print(f"→ Gemini API ({model}) · slug '{slug}'...")
    resp = None
    for attempt in range(retries + 1):
        try:
            req = request.Request(url, data=payload, headers=headers, method="POST")
            with request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read())
            break
        except error.HTTPError as e:
            body_txt = e.read().decode("utf-8", errors="ignore")[:300]
            err_msg = f"HTTP {e.code}: {body_txt}"
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                wait = 2 ** attempt
                print(f"  ! {err_msg} — retry em {wait}s")
                time.sleep(wait)
                continue
            sys.exit(f"Gemini falhou: {err_msg}")

    img_b64 = None
    if resp:
        for cand in resp.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    img_b64 = inline["data"]
                    break
            if img_b64:
                break
    if not img_b64:
        sys.exit(f"Gemini retornou sem imagem. resposta: {json.dumps(resp)[:500]}")

    out_dir = BANK / slug / "ai"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("*.png"))) + 1
    out_path = out_dir / f"{n}.png"
    out_path.write_bytes(base64.b64decode(img_b64))

    meta_path = out_dir / "_meta.json"
    meta: dict = {"entries": []}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    meta.setdefault("entries", []).append(
        {
            "file": out_path.name,
            "mode": "api",
            "model": model,
            "prompt": prompt,
            "aspect": aspect_ratio,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "saved": True,
        }
    )
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  ✓ {out_path.relative_to(ROOT)} ({out_path.stat().st_size // 1024} KB)")
    return out_path


def cmd_api(args: argparse.Namespace) -> None:
    generate_via_api(
        args.prompt,
        slug=args.slug,
        model=args.model,
        aspect_ratio=args.aspect_ratio,
    )


# ---- CLI -------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prompt", help="Gera prompt padronizado pra uso manual no Gemini web")
    p.add_argument("--slug", required=True, help="ex: garmin_fr965")
    p.add_argument(
        "--preset",
        required=True,
        choices=list(PRESETS.keys()),
        help="; ".join(f"{k}: {v['description']}" for k, v in PRESETS.items()),
    )
    p.add_argument("--subject", help="sujeito principal (obrigatório em todos os presets exceto mockup)")
    p.add_argument("--detail", help="detalhes específicos (cor, pose, equipamento)")
    p.add_argument("--location", help="bg_brazil — local específico")
    p.add_argument("--time-of-day", dest="time_of_day", help="bg_brazil — momento (default: golden hour)")
    p.add_argument("--angle", help="product — ângulo (default: 3/4)")
    p.add_argument("--neg-space", dest="neg_space", help="bg_* — onde fica espaço pra texto")
    p.add_argument("--device", help="mockup — dispositivo")
    p.add_argument("--interface", help="mockup — tela mostrada")
    p.add_argument("--aspect-ratio", dest="aspect_ratio", help="override aspect ratio do preset")
    p.set_defaults(func=cmd_prompt)

    a = sub.add_parser("api", help="Chama Gemini API direto (requer billing)")
    a.add_argument("--slug", required=True)
    a.add_argument("--prompt", required=True)
    a.add_argument("--model", default=DEFAULT_MODEL)
    a.add_argument("--aspect-ratio", dest="aspect_ratio", default=None)
    a.set_defaults(func=cmd_api)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
