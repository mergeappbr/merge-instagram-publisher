"""Cleanup R2 — apaga objetos antigos pra ficar dentro do free tier (10GB).

Política por prefixo (idade > N dias):
  - news_bg/        → 14 dias  (BG do post; Instagram já hospeda interno)
  - ig/posts/       →  7 dias  (asset publicado; IG hospeda interno)
  - ig/reels/       →  7 dias
  - ig/stories/     →  3 dias  (story vive 24h; +2d folga pra ranking)
  - approvals_state → não mexe (auto-cleanup ao aprovar/rejeitar)

Cap de segurança: máx 500 deletes por execução (defesa contra bug).
Dry-run: imprime mas não apaga.

Uso programático (do scheduler):
  from r2_cleanup import maybe_run
  maybe_run(now)

Uso CLI:
  python3 src/r2_cleanup.py --dry-run
  python3 src/r2_cleanup.py
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "output" / ".last_r2_cleanup.txt"

# Política — (prefixo, idade_máxima_dias)
RETENTION = [
    ("news_bg/", 14),
    ("ig/posts/", 7),
    ("ig/reels/", 7),
    ("ig/stories/", 3),
]

MAX_DELETES_PER_RUN = 500
RUN_INTERVAL_HOURS = 24


def _client():
    """Cria cliente boto3 R2. Retorna None se env vars faltarem."""
    required = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        print(f"⚠ r2_cleanup: faltam env vars {missing}, pulando")
        return None, None
    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError:
        print("⚠ r2_cleanup: boto3 não instalado")
        return None, None
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
    )
    return client, os.environ["R2_BUCKET"]


def _list_old_keys(client, bucket: str, prefix: str, max_age_days: int) -> list[str]:
    """Lista keys do prefixo com LastModified > max_age_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            if obj["LastModified"] < cutoff:
                keys.append(obj["Key"])
            if len(keys) >= MAX_DELETES_PER_RUN:
                return keys
    return keys


def _delete_batch(client, bucket: str, keys: list[str]) -> int:
    """delete_objects aceita até 1000 keys/chamada. Retorna número apagado."""
    if not keys:
        return 0
    deleted = 0
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        resp = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        errors = resp.get("Errors") or []
        for err in errors:
            print(f"⚠ r2_cleanup delete falhou: {err.get('Key')}: {err.get('Message')}")
        deleted += len(batch) - len(errors)
    return deleted


def cleanup_once(*, dry_run: bool = False) -> dict:
    """Uma passada. Retorna stats por prefixo."""
    client, bucket = _client()
    if not client:
        return {"skipped": True}
    stats: dict[str, dict] = {}
    total_deleted = 0
    for prefix, max_age in RETENTION:
        keys = _list_old_keys(client, bucket, prefix, max_age)
        n = len(keys)
        if dry_run:
            print(f"[dry-run] {prefix} → {n} key(s) > {max_age}d apagariam")
            stats[prefix] = {"found": n, "deleted": 0, "max_age_days": max_age}
            continue
        deleted = _delete_batch(client, bucket, keys)
        stats[prefix] = {"found": n, "deleted": deleted, "max_age_days": max_age}
        total_deleted += deleted
        if deleted:
            print(f"r2_cleanup · {prefix} → {deleted}/{n} apagado(s) (>{max_age}d)")
    stats["total_deleted"] = total_deleted
    return stats


def maybe_run(now: datetime) -> bool:
    """Roda no máx 1x por dia (RUN_INTERVAL_HOURS). Retorna True se rodou."""
    if STATE_FILE.exists():
        try:
            last = datetime.fromisoformat(STATE_FILE.read_text(encoding="utf-8").strip())
            if last.tzinfo is None and now.tzinfo is not None:
                last = last.replace(tzinfo=now.tzinfo)
            if (now - last).total_seconds() < RUN_INTERVAL_HOURS * 3600:
                return False
        except ValueError:
            pass
    try:
        stats = cleanup_once(dry_run=False)
        if stats.get("skipped"):
            return False
        if stats.get("total_deleted", 0) > 0:
            print(f"r2_cleanup · total apagado: {stats['total_deleted']}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ r2_cleanup exception: {e!r}")
        return False
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="lista o que apagaria sem apagar")
    args = p.parse_args(argv)
    stats = cleanup_once(dry_run=args.dry_run)
    if stats.get("skipped"):
        return 1
    print(f"\nresumo: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
