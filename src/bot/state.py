"""
State persistente do bot — sobrevive a restart do worker.

Tudo vive em output/bot_state/ (volume Railway):
  bot_state/
    offset.txt                 # último update_id processado
    pending/<id>.json          # approval pendente (brief ou story)
    awaiting/<chat_id>.json    # apontador: chat X está aguardando texto pra approval Y
    archive/<id>.json          # approvals decididos (aprovados/rejeitados)

Approvals têm TTL implícito: a Fase B trata aging via job de limpeza.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = ROOT / "output" / "bot_state"
OFFSET_FILE = STATE_DIR / "offset.txt"
PENDING_DIR = STATE_DIR / "pending"
AWAITING_DIR = STATE_DIR / "awaiting"
ARCHIVE_DIR = STATE_DIR / "archive"


def _ensure_dirs() -> None:
    for d in (STATE_DIR, PENDING_DIR, AWAITING_DIR, ARCHIVE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# -------------------- offset --------------------

def get_offset() -> int | None:
    if not OFFSET_FILE.exists():
        return None
    try:
        return int(OFFSET_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def set_offset(value: int) -> None:
    _ensure_dirs()
    OFFSET_FILE.write_text(str(value), encoding="utf-8")


# -------------------- approvals --------------------

def new_approval_id() -> str:
    """ID curto pra caber em callback_data (limite 64 bytes)."""
    return uuid.uuid4().hex[:10]


def write_approval(approval: dict) -> None:
    _ensure_dirs()
    aid = approval["id"]
    path = PENDING_DIR / f"{aid}.json"
    path.write_text(
        json.dumps(approval, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_approval(aid: str) -> dict | None:
    path = PENDING_DIR / f"{aid}.json"
    if not path.exists():
        # Pode ter ido pro archive
        archived = ARCHIVE_DIR / f"{aid}.json"
        if archived.exists():
            return json.loads(archived.read_text(encoding="utf-8"))
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def list_pending() -> list[dict]:
    _ensure_dirs()
    items: list[dict] = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return items


def archive_approval(aid: str, *, decision: str, extra: dict[str, Any] | None = None) -> None:
    """Move approval pra arquivo, registra decisão."""
    src = PENDING_DIR / f"{aid}.json"
    if not src.exists():
        return
    payload = json.loads(src.read_text(encoding="utf-8"))
    payload["decision"] = decision
    payload["decided_at"] = int(time.time())
    if extra:
        payload.update(extra)
    dst = ARCHIVE_DIR / f"{aid}.json"
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    src.unlink(missing_ok=True)


# -------------------- awaiting text --------------------
# Quando user clica "✏️ Ajustar", marcamos o chat como aguardando texto livre.

def set_awaiting(chat_id: int | str, approval_id: str, kind: str = "adjust") -> None:
    _ensure_dirs()
    path = AWAITING_DIR / f"{chat_id}.json"
    path.write_text(
        json.dumps({"approval_id": approval_id, "kind": kind, "ts": int(time.time())}),
        encoding="utf-8",
    )


def get_awaiting(chat_id: int | str) -> dict | None:
    path = AWAITING_DIR / f"{chat_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def clear_awaiting(chat_id: int | str) -> None:
    path = AWAITING_DIR / f"{chat_id}.json"
    path.unlink(missing_ok=True)
