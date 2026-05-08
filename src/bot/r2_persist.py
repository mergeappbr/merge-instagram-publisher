"""
Persistência de approval state em Cloudflare R2.

Railway tem filesystem ephemeral — todo deploy reseta `output/`. Approvals de
news perdem o PNG e o JSON na hora do redeploy. Esse módulo faz backup do
state crítico (approval JSON + PNG do feed) em R2 quando o preview é mandado
no Telegram, e restaura quando o usuário clica "Postar agora" — mesmo que o
container do Railway tenha sido reiniciado nesse meio tempo.

Chaves R2:
  approvals_state/<aid>.json   → approval dict (inclui brief, news_context, etc)
  approvals_state/<aid>.png    → PNG do feed renderizado

Cleanup: chama `delete_backup(aid)` após publicar com sucesso.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
PENDING_DIR = ROOT / "output" / "bot_state" / "pending"
FEED_DIR = ROOT / "output" / "feed"
R2_PREFIX = "approvals_state"


def _client():
    """Lazy-import boto3 + monta client R2. Falha cedo se env faltar."""
    import boto3  # type: ignore
    from botocore.config import Config  # type: ignore
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    return os.environ["R2_BUCKET"]


def backup(aid: str, png_path: Optional[Path] = None) -> bool:
    """Sobe approval JSON (+ PNG opcional) pra R2. Idempotente.

    Chamar logo após o preview ser enviado no Telegram. Retorna True se ao
    menos o JSON foi pra R2.
    """
    pending = PENDING_DIR / f"{aid}.json"
    if not pending.exists():
        print(f"⚠ r2_persist.backup: pending {aid}.json não existe")
        return False
    try:
        client = _client()
        bucket = _bucket()
        client.upload_file(
            str(pending),
            bucket,
            f"{R2_PREFIX}/{aid}.json",
            ExtraArgs={"ContentType": "application/json"},
        )
        if png_path and png_path.exists():
            client.upload_file(
                str(png_path),
                bucket,
                f"{R2_PREFIX}/{aid}.png",
                ExtraArgs={"ContentType": "image/png"},
            )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠ r2_persist.backup falhou ({aid}): {e!r}")
        return False


def restore_approval(aid: str) -> Optional[dict]:
    """Baixa approval JSON do R2 (se faltar localmente) e devolve o dict.

    Também restaura o PNG do feed em output/feed/<brief_id>.png se faltar.
    Retorna None se nem R2 tem o backup.
    """
    pending = PENDING_DIR / f"{aid}.json"
    approval: dict | None = None

    if pending.exists():
        try:
            approval = json.loads(pending.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            approval = None

    if approval is None:
        try:
            client = _client()
            PENDING_DIR.mkdir(parents=True, exist_ok=True)
            client.download_file(_bucket(), f"{R2_PREFIX}/{aid}.json", str(pending))
            approval = json.loads(pending.read_text(encoding="utf-8"))
            print(f"↓ r2_persist: restaurou approval {aid} do R2")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ r2_persist.restore_approval falhou ({aid}): {e!r}")
            return None

    # Tenta restaurar PNG também
    brief = approval.get("brief") or {}
    bid = brief.get("id")
    if bid:
        local_png = FEED_DIR / f"{bid}.png"
        if not local_png.exists():
            try:
                client = _client()
                FEED_DIR.mkdir(parents=True, exist_ok=True)
                client.download_file(_bucket(), f"{R2_PREFIX}/{aid}.png", str(local_png))
                print(f"↓ r2_persist: restaurou PNG {bid} do R2")
            except Exception as e:  # noqa: BLE001
                print(f"⚠ r2_persist PNG restore falhou ({bid}): {e!r}")
                # Não fatal — caller pode re-renderizar a partir do brief
    return approval


def delete_backup(aid: str) -> None:
    """Apaga backup do R2 (chamar pós-publicação ou rejeição)."""
    try:
        client = _client()
        bucket = _bucket()
        client.delete_object(Bucket=bucket, Key=f"{R2_PREFIX}/{aid}.json")
        client.delete_object(Bucket=bucket, Key=f"{R2_PREFIX}/{aid}.png")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ r2_persist.delete_backup falhou ({aid}): {e!r}")
