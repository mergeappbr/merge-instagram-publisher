"""
Publish Instagram media via Meta Graph API.

Suporta 3 formatos:
  - static  : 1 imagem  (ex: --post 38)
  - carousel: 2-10 imgs (ex: --post 37, com 37.png + 37.2.png + …)
  - reel    : 1 vídeo   (ex: --post reel_zonas, lê output/reels/<id>.mp4)

Fluxo:
  1. (opcional) sobe assets locais pra um bucket S3-compatível (Cloudflare R2)
  2. cria container(s) no Graph API (item per slide pra carrossel)
  3. publica via /media_publish (ou agenda com scheduled_publish_time)
  4. loga em output/published.csv

Uso típico:

  # static (1 imagem) — auto-detecta pelo nº de arquivos
  python3 src/publish.py \\
      "$HOME/Desktop/Merge - Posts Semanais/Semana 09 - Bike (42-46)/feed" \\
      --post 42

  # carrossel (slides X.png, X.2.png, …) — também auto-detecta
  python3 src/publish.py \\
      "$HOME/Desktop/Merge - Posts Semanais/Semana 08 - Corrida (37-41)/feed" \\
      --post 37

  # reel — basta passar --post reel_<nome>; pega MP4 de output/reels/
  python3 src/publish.py --post reel_zonas

  # agendar (até 75 dias)
  python3 src/publish.py --post reel_zonas --schedule "2026-05-10 09:00"

  # dry-run pra inspecionar payload sem publicar
  python3 src/publish.py --post 38 --dry-run

Env vars necessárias (.env na raiz):
  META_GRAPH_ACCESS_TOKEN   Page Access Token (long-lived) da Page Merge
  IG_BUSINESS_ACCOUNT_ID    IG Business ID do @mergeapp.wellness
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
  R2_BUCKET, R2_PUBLIC_BASE_URL (ou R2_PUBLIC_BASE — qualquer um dos dois)
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GRAPH_BASE = "https://graph.facebook.com/v21.0"
ACCESS_TOKEN = os.getenv("META_GRAPH_ACCESS_TOKEN", "")
IG_USER_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID", "")

DEFAULT_CAPTIONS = ROOT / "content" / "captions.md"
# Em ordem: env override → Desktop do Pedro → espelho dentro do repo (Railway-friendly)
FEED_BASE_CANDIDATES = [
    Path(os.environ["MERGE_FEED_BASE"]) if os.getenv("MERGE_FEED_BASE") else None,
    Path("/Users/pedrowanderleyalmeida/Desktop/Merge - Posts Semanais"),
    ROOT / "posts",
]
REELS_SRC = ROOT / "output" / "reels"
PUBLISH_LOG = ROOT / "output" / "published.csv"

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4"}
MAX_CAROUSEL = 10  # Instagram limit
REEL_WAIT_TIMEOUT = 600  # reels demoram mais a processar


# ---------------------------------------------------------------------------
# R2 upload (S3 compatible)
# ---------------------------------------------------------------------------

_TRAILING_GARBAGE = "\"' \t\r\n/%"


def _resolve_public_base() -> str:
    """Lê R2_PUBLIC_BASE_URL (ou fallback R2_PUBLIC_BASE), tira aspas/espaços/
    barras e qualquer % residual no final. Valida que começa com https://."""
    raw = (
        os.environ.get("R2_PUBLIC_BASE_URL")
        or os.environ.get("R2_PUBLIC_BASE")
        or ""
    )
    cleaned = raw.strip().strip('"').strip("'")
    # Tira lixo no final (% url-encoded mal colado, barras, aspas)
    while cleaned and cleaned[-1] in _TRAILING_GARBAGE:
        cleaned = cleaned[:-1]
    if not cleaned.startswith(("https://", "http://")):
        sys.exit(
            f"R2_PUBLIC_BASE_URL inválida (precisa começar com https://): {raw!r}"
        )
    return cleaned


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
    }.get(ext, "application/octet-stream")


def upload_to_r2(local_paths: list[Path], prefix: str) -> list[str]:
    """Sobe arquivos pro R2 e retorna URLs públicas."""
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError:
        sys.exit("boto3 não instalado. Rode: pip install boto3")

    account_id = os.environ["R2_ACCOUNT_ID"]
    access_key = os.environ["R2_ACCESS_KEY_ID"]
    secret_key = os.environ["R2_SECRET_ACCESS_KEY"]
    bucket = os.environ["R2_BUCKET"]
    public_base = _resolve_public_base()

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
    )

    urls: list[str] = []
    for path in local_paths:
        key = f"{prefix}/{path.name}"
        client.upload_file(
            str(path),
            bucket,
            key,
            ExtraArgs={
                "ContentType": _content_type(path),
                "CacheControl": "public, max-age=31536000",
            },
        )
        url = f"{public_base}/{key}"
        urls.append(url)
        print(f"  ↑ R2: {url}")
    return urls


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

def _post(client: httpx.Client, path: str, data: dict) -> dict:
    data = {**data, "access_token": ACCESS_TOKEN}
    resp = client.post(f"{GRAPH_BASE}/{path}", data=data, timeout=60)
    if resp.status_code >= 400:
        sys.exit(f"Graph API {resp.status_code}: {resp.text}")
    return resp.json()


def _get(client: httpx.Client, path: str, params: dict | None = None) -> dict:
    params = {**(params or {}), "access_token": ACCESS_TOKEN}
    resp = client.get(f"{GRAPH_BASE}/{path}", params=params, timeout=30)
    if resp.status_code >= 400:
        sys.exit(f"Graph API {resp.status_code}: {resp.text}")
    return resp.json()


def create_static_container(
    client: httpx.Client,
    image_url: str,
    caption: str,
    scheduled_publish_time: int | None = None,
) -> str:
    payload = {"image_url": image_url, "caption": caption}
    if scheduled_publish_time is not None:
        payload["published"] = "false"
        payload["scheduled_publish_time"] = str(scheduled_publish_time)
    return _post(client, f"{IG_USER_ID}/media", payload)["id"]


def create_carousel_item(client: httpx.Client, image_url: str) -> str:
    return _post(client, f"{IG_USER_ID}/media", {
        "image_url": image_url,
        "is_carousel_item": "true",
    })["id"]


def create_carousel_container(
    client: httpx.Client,
    children_ids: list[str],
    caption: str,
    scheduled_publish_time: int | None = None,
) -> str:
    payload = {
        "media_type": "CAROUSEL",
        "children": ",".join(children_ids),
        "caption": caption,
    }
    if scheduled_publish_time is not None:
        payload["published"] = "false"
        payload["scheduled_publish_time"] = str(scheduled_publish_time)
    return _post(client, f"{IG_USER_ID}/media", payload)["id"]


def create_reel_container(
    client: httpx.Client,
    video_url: str,
    caption: str,
    scheduled_publish_time: int | None = None,
    share_to_feed: bool = True,
) -> str:
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true" if share_to_feed else "false",
    }
    if scheduled_publish_time is not None:
        payload["published"] = "false"
        payload["scheduled_publish_time"] = str(scheduled_publish_time)
    return _post(client, f"{IG_USER_ID}/media", payload)["id"]


def create_story_container(
    client: httpx.Client,
    image_url: str,
) -> str:
    """Container pra IG Stories (imagem). Sem caption — stories não usa."""
    payload = {
        "media_type": "STORIES",
        "image_url": image_url,
    }
    return _post(client, f"{IG_USER_ID}/media", payload)["id"]


def wait_until_finished(client: httpx.Client, container_id: str, timeout_s: int = 180) -> None:
    """Aguarda container ficar FINISHED. Resiliente a Graph 100/33 (mai/2026):
    Meta passou a bloquear GET em containers com Page Token (erro
    GraphMethodException 100/33). Quando isso ocorre, fazemos *blind wait*:
    25s pra IMAGE (slide / static), 60s pra REEL. Continua erro real (ERROR /
    EXPIRED) abortando como antes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        params = {"fields": "status_code,status", "access_token": ACCESS_TOKEN}
        resp = client.get(f"{GRAPH_BASE}/{container_id}", params=params, timeout=30)
        if resp.status_code >= 400:
            body = resp.text
            # Meta passou a bloquear GET em containers com Page Token
            # (100/33). Cai pra blind wait.
            if '"code":100' in body and ('"error_subcode":33' in body or 'GraphMethodException' in body):
                blind_wait_s = 60 if timeout_s >= REEL_WAIT_TIMEOUT else 25
                print(f"  ⚠ GET status bloqueado (Meta 100/33) — blind wait {blind_wait_s}s")
                time.sleep(blind_wait_s)
                return
            sys.exit(f"Graph API {resp.status_code}: {body}")
        info = resp.json()
        status = info.get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Container {container_id} falhou: {info}")
        time.sleep(3)
    sys.exit(f"Timeout esperando container {container_id}")


def publish_container(client: httpx.Client, creation_id: str) -> str:
    return _post(client, f"{IG_USER_ID}/media_publish", {"creation_id": creation_id})["id"]


# ---------------------------------------------------------------------------
# Stories (API pública pro news pipeline)
# ---------------------------------------------------------------------------

def publish_story(local_image_path: str, *, post_id: str, prefix: str | None = None) -> str:
    """
    Publica IG Story a partir de uma imagem local (PNG/JPG).
    Sobe pro R2 → cria container STORIES → publica → loga.

    Retorna media_id. Levanta SystemExit em falha (mesmo padrão do publish.py).

    Uso (do news pipeline):
        from publish import publish_story
        media_id = publish_story("output/stories/story_news_xxx.png", post_id="story_news_xxx")
    """
    if not ACCESS_TOKEN or not IG_USER_ID:
        sys.exit("Faltam META_GRAPH_ACCESS_TOKEN ou IG_BUSINESS_ACCOUNT_ID")
    img = Path(local_image_path)
    if not img.exists():
        sys.exit(f"Story image não encontrada: {img}")

    pfx = prefix or f"ig/stories/{post_id}/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"→ STORY: subindo 1 imagem para R2 (prefix: {pfx})")
    urls = upload_to_r2([img], pfx)
    image_url = urls[0]

    with httpx.Client() as client:
        print("→ Criando container STORIES…")
        creation_id = create_story_container(client, image_url)
        print(f"  · {creation_id}")
        wait_until_finished(client, creation_id)
        print("→ Publicando story…")
        media_id = publish_container(client, creation_id)
        print(f"✓ Story publicado! media_id={media_id}")

    log_publication({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "media_id": media_id,
        "creation_id": creation_id,
        "format": "story",
        "post_id": post_id,
        "assets": img.name,
        "caption_preview": "",
        "scheduled_for": "",
    })
    return media_id


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_publication(row: dict) -> None:
    PUBLISH_LOG.parent.mkdir(parents=True, exist_ok=True)
    is_new = not PUBLISH_LOG.exists()
    with PUBLISH_LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "timestamp", "media_id", "creation_id", "format",
            "post_id", "assets", "caption_preview", "scheduled_for",
        ])
        if is_new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------------------
# Asset collection
# ---------------------------------------------------------------------------

def collect_post_images(folder: Path, post_id: str) -> list[Path]:
    """Pega `post_id.png` + `post_id.2.png`, … na ordem certa."""
    norm = post_id.lstrip("0") or "0"
    candidates = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        stem = p.stem.lstrip("0") or "0"
        if stem == norm or stem.startswith(f"{norm}."):
            candidates.append(p)

    def order_key(p: Path) -> tuple[int, str]:
        stem = p.stem
        if "." in stem:
            _, idx = stem.split(".", 1)
            return (int(idx) if idx.isdigit() else 99, stem)
        return (1, stem)

    candidates.sort(key=order_key)
    if not candidates:
        sys.exit(f"Nenhuma imagem para post {post_id} em {folder}")
    if len(candidates) > MAX_CAROUSEL:
        sys.exit(f"Carrossel suporta no máximo {MAX_CAROUSEL} slides")
    return candidates


def find_feed_folder_for_post(post_id: str) -> Path:
    """Acha a pasta que contém `<post_id>.png`.

    Ordem:
      1. `output/feed/` (flat — autogen, news, ironman renderizam aqui direto)
      2. `Semana XX/feed/` dentro de cada base do FEED_BASE_CANDIDATES
         (estrutura semanal do pipeline manual)
    """
    norm = post_id.lstrip("0") or "0"
    target = f"{norm}.png"

    # 1) Layout flat (output/feed/<id>.png) — usado por todo o fluxo automático
    flat_dir = ROOT / "output" / "feed"
    if (flat_dir / target).exists() or (flat_dir / f"{post_id}.png").exists():
        return flat_dir

    # 2) Layout semanal (Semana XX/feed/<id>.png) — pipeline manual
    tried: list[Path] = []
    for base in FEED_BASE_CANDIDATES:
        if base is None or not base.exists():
            continue
        tried.append(base)
        for week_dir in sorted(base.iterdir()):
            feed_dir = week_dir / "feed"
            if not feed_dir.is_dir():
                continue
            if (feed_dir / target).exists():
                return feed_dir
    sys.exit(
        f"Não achei {target} em output/feed/ nem em nenhuma Semana XX/feed/ "
        f"dentro de {tried or 'pasta nenhuma (todas inexistentes)'}"
    )


def find_story_for_post(post_id: str, feed_folder: Path | None) -> Path | None:
    """Acha o PNG do story que acompanha o post.

    Ordem de busca:
      1. Se feed_folder veio de Semana XX/feed/, tenta Semana XX/stories/<NN>.png
         (post numerado semanal — convenção em ~/Desktop/Merge - Posts Semanais/)
      2. Tenta output/stories/<post_id>.png (autogen briefs renderizados)

    Retorna None se nenhum existir — caller deve pular silenciosamente.
    """
    norm = post_id.lstrip("0") or "0"
    if feed_folder is not None:
        story_dir = feed_folder.parent / "stories"
        # Tenta com e sem zero à esquerda (Semana 02 tem 06.png e 6.png pode aparecer)
        for name in (f"{norm}.png", f"{post_id}.png"):
            cand = story_dir / name
            if cand.exists():
                return cand
    cand = ROOT / "output" / "stories" / f"{post_id}.png"
    if cand.exists():
        return cand
    return None


def find_reel_video(reel_id: str) -> Path:
    """Acha o MP4 do reel em output/reels/."""
    p = REELS_SRC / f"{reel_id}.mp4"
    if not p.exists():
        sys.exit(f"Reel não encontrado: {p}")
    return p


# ---------------------------------------------------------------------------
# captions.md parser
# ---------------------------------------------------------------------------

# Match `## NN ·` (numerados), `## reel_xxx ·` (reels), `## news_xxx ·`,
# `## ironman_xxx ·` etc. — qualquer id alfanumérico com underscores.
CAPTION_HEADING_RE = re.compile(r"^##\s+([a-z0-9_]+)\b.*$", re.IGNORECASE)
# Match qualquer linha de metadata interna: **Hook do post:**, **Story tip:**,
# **Hook do reel:**, **Caption:**, etc. Tudo que começa com **Xxx:** é
# diretiva interna pro time/AI e NUNCA deve vazar pra legenda do IG.
CAPTION_META_RE = re.compile(r"^\*\*[^*\n]+:\*\*")


def caption_from_md(captions_path: Path, post_id: str) -> str:
    """
    Extrai a legenda da seção `## NN · …` ou `## reel_xxx · …` do captions.md.
    Pula `**Hook do post:**` / `**Hook do reel:**` (texto já no slide).
    Para no próximo `---` ou `## ` heading.
    """
    if not captions_path.exists():
        sys.exit(f"captions.md não encontrado em {captions_path}")

    if post_id.startswith("reel_"):
        target = post_id.lower()
    else:
        target = post_id.lstrip("0") or "0"

    body_lines: list[str] = []
    in_section = False
    for line in captions_path.read_text(encoding="utf-8").splitlines():
        m = CAPTION_HEADING_RE.match(line)
        if m:
            if in_section:
                break
            tag = m.group(1).lower()
            tag_norm = tag if tag.startswith("reel_") else (tag.lstrip("0") or "0")
            if tag_norm == target:
                in_section = True
                continue
            continue
        if not in_section:
            continue
        if line.strip() == "---":
            break
        ls = line.lstrip()
        # Filtra QUALQUER linha de metadata `**Xxx:**` (Hook do post, Story tip,
        # Hook do reel, etc) — são diretivas internas, nunca vão pro IG.
        if CAPTION_META_RE.match(ls):
            continue
        body_lines.append(line)

    if not in_section:
        sys.exit(f"Seção `## {post_id} ·` não encontrada em {captions_path}")
    text = "\n".join(body_lines).strip()
    if not text:
        sys.exit(f"Legenda vazia para post {post_id}")
    return text


def parse_schedule(value: str) -> int:
    """'2026-05-10 07:00' (hora local) → unix timestamp."""
    return int(datetime.strptime(value, "%Y-%m-%d %H:%M").timestamp())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Publica static / carousel / reel no Instagram")
    parser.add_argument("folder", nargs="?", help="Pasta do feed (default: auto-acha em ~/Desktop/Merge - Posts Semanais)")
    parser.add_argument("--post", required=True, help="NN (post numerado) ou reel_<nome>")
    parser.add_argument("--captions", default=str(DEFAULT_CAPTIONS))
    parser.add_argument("--caption", help="Texto da legenda (override)")
    parser.add_argument("--caption-file", help="Arquivo .txt com legenda (override)")
    parser.add_argument("--schedule", help="'AAAA-MM-DD HH:MM' (max 75 dias)")
    parser.add_argument("--prefix", help="Prefixo do path no R2")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not ACCESS_TOKEN or not IG_USER_ID:
        sys.exit("Faltam META_GRAPH_ACCESS_TOKEN ou IG_BUSINESS_ACCOUNT_ID no .env")

    is_reel = args.post.startswith("reel_")

    # --- legenda ------------------------------------------------------------
    if args.caption_file:
        caption = Path(args.caption_file).read_text(encoding="utf-8").strip()
    elif args.caption:
        caption = args.caption
    else:
        caption = caption_from_md(Path(args.captions).expanduser().resolve(), args.post)

    schedule_ts = parse_schedule(args.schedule) if args.schedule else None
    if schedule_ts:
        print(f"→ Agendado para {datetime.fromtimestamp(schedule_ts).isoformat()}")

    # --- coleta de assets ---------------------------------------------------
    if is_reel:
        video_path = find_reel_video(args.post)
        prefix = args.prefix or f"ig/reels/{args.post}/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"→ Subindo vídeo do reel para R2 (prefix: {prefix})")
        urls = upload_to_r2([video_path], prefix)
        video_url = urls[0]
        media_format = "reel"
        assets_label = video_path.name
    else:
        folder = Path(args.folder).expanduser().resolve() if args.folder else find_feed_folder_for_post(args.post)
        files = collect_post_images(folder, args.post)
        media_format = "static" if len(files) == 1 else "carousel"
        prefix = args.prefix or f"ig/post{args.post}/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"→ {media_format.upper()}: subindo {len(files)} imagem(s) para R2 (prefix: {prefix})")
        image_urls = upload_to_r2(files, prefix)
        assets_label = ", ".join(p.name for p in files)

    # --- Graph API flow -----------------------------------------------------
    with httpx.Client() as client:
        if media_format == "reel":
            print("→ Criando container do reel…")
            creation_id = create_reel_container(client, video_url, caption, schedule_ts)
            print(f"  · {creation_id}")
            print("→ Aguardando processamento (pode demorar)…")
            wait_until_finished(client, creation_id, REEL_WAIT_TIMEOUT)
        elif media_format == "static":
            print("→ Criando container do post…")
            creation_id = create_static_container(client, image_urls[0], caption, schedule_ts)
            print(f"  · {creation_id}")
            wait_until_finished(client, creation_id)
        else:  # carousel
            print("→ Criando containers de cada slide…")
            children = []
            for url in image_urls:
                cid = create_carousel_item(client, url)
                print(f"  · {cid}  ({url})")
                children.append(cid)
            print("→ Aguardando processamento dos slides…")
            for cid in children:
                wait_until_finished(client, cid)
            print("→ Criando container do carrossel…")
            creation_id = create_carousel_container(client, children, caption, schedule_ts)
            print(f"  · {creation_id}")
            wait_until_finished(client, creation_id)

        if args.dry_run:
            print("⚠ dry-run: parando antes do /media_publish")
            return

        if schedule_ts:
            media_id = ""
            print(f"✓ {media_format.capitalize()} agendado (creation_id={creation_id})")
        else:
            print("→ Publicando…")
            media_id = publish_container(client, creation_id)
            print(f"✓ Publicado! media_id={media_id}")

    log_publication({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "media_id": media_id,
        "creation_id": creation_id,
        "format": media_format,
        "post_id": args.post,
        "assets": assets_label,
        "caption_preview": caption[:120].replace("\n", " "),
        "scheduled_for": datetime.fromtimestamp(schedule_ts).isoformat() if schedule_ts else "",
    })

    # --- auto-publica story acompanhando o feed -----------------------------
    # Stories só sobem quando o post é IMAGEM (static/carousel), não-agendado
    # e não-dry-run. Para reels não há "story irmã" no padrão. Para schedule_ts
    # o IG Graph não suporta agendar stories, então publicamos junto-do-feed
    # apenas em publish imediato. Falha aqui NÃO derruba o publish do feed
    # (já registrado em published.csv).
    if not is_reel and not args.dry_run and not schedule_ts:
        try:
            story_png = find_story_for_post(args.post, folder)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ falha procurando story do post {args.post}: {e!r}")
            story_png = None
        if story_png is None:
            print(f"ℹ nenhum story PNG pareado para post {args.post} — pulando")
        else:
            print(f"→ STORY: publicando {story_png.name} (companheiro do feed)")
            try:
                story_post_id = f"{args.post}_story"
                publish_story(str(story_png), post_id=story_post_id)
            except SystemExit as e:
                print(f"⚠ story não publicado para {args.post}: {e}")
            except Exception as e:  # noqa: BLE001
                print(f"⚠ story falhou para {args.post}: {e!r}")


if __name__ == "__main__":
    main()
