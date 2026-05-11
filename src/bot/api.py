"""
Wrapper minimalista da Telegram Bot API.

Não usa SDK externo (mantém deps enxutas). Usa httpx pra paridade com alerts.py.

Convenções:
  - parse_mode HTML em todas as mensagens (igual alerts.py)
  - Strings dinâmicas DEVEM ser pré-escapadas com html.escape() pelo caller
  - Retorna dict de resposta da API; em erro, retorna {} e loga warning
"""
from __future__ import annotations

import os
from typing import Any

import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 30.0


def _post(method: str, payload: dict, *, timeout: float = DEFAULT_TIMEOUT) -> dict:
    if not BOT_TOKEN:
        return {}
    try:
        r = httpx.post(
            f"{API_BASE}/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ telegram.{method} exception: {e!r}")
        return {}
    if r.status_code != 200:
        print(f"⚠ telegram.{method} {r.status_code}: {r.text[:200]}")
        return {}
    return r.json()


def send_message(
    text: str,
    *,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    silent: bool = False,
    reply_to: int | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id or CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if reply_to is not None:
        payload["reply_to_message_id"] = reply_to
    return _post("sendMessage", payload)


def send_photo(
    photo_url: str,
    *,
    caption: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    silent: bool = False,
) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id or CHAT_ID,
        "photo": photo_url,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }
    if caption:
        payload["caption"] = caption
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _post("sendPhoto", payload)


def send_photo_file(
    photo_path: str,
    *,
    caption: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    silent: bool = False,
) -> dict:
    """Upload de arquivo local pro Telegram (multipart). Útil pra previews offline."""
    if not BOT_TOKEN:
        return {}
    data: dict[str, Any] = {
        "chat_id": chat_id or CHAT_ID,
        "parse_mode": "HTML",
        "disable_notification": "true" if silent else "false",
    }
    if caption:
        data["caption"] = caption
    if reply_markup is not None:
        import json as _json
        data["reply_markup"] = _json.dumps(reply_markup)
    try:
        with open(photo_path, "rb") as f:
            r = httpx.post(
                f"{API_BASE}/bot{BOT_TOKEN}/sendPhoto",
                data=data,
                files={"photo": f},
                timeout=60.0,
            )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ telegram.sendPhoto upload exception: {e!r}")
        return {}
    if r.status_code != 200:
        print(f"⚠ telegram.sendPhoto upload {r.status_code}: {r.text[:200]}")
        return {}
    return r.json()


def send_document_file(
    doc_path: str,
    *,
    caption: str | None = None,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    silent: bool = False,
) -> dict:
    """Upload de arquivo local como document (sem compressão/letterbox do Telegram).

    Usado pros previews finais onde precisão visual importa — Telegram trata
    document como arquivo: thumbnail consistente, sem auto-crop, full-res.
    """
    if not BOT_TOKEN:
        return {}
    data: dict[str, Any] = {
        "chat_id": chat_id or CHAT_ID,
        "parse_mode": "HTML",
        "disable_notification": "true" if silent else "false",
    }
    if caption:
        data["caption"] = caption
    if reply_markup is not None:
        import json as _json
        data["reply_markup"] = _json.dumps(reply_markup)
    try:
        with open(doc_path, "rb") as f:
            r = httpx.post(
                f"{API_BASE}/bot{BOT_TOKEN}/sendDocument",
                data=data,
                files={"document": f},
                timeout=90.0,
            )
    except Exception as e:  # noqa: BLE001
        print(f"⚠ telegram.sendDocument upload exception: {e!r}")
        return {}
    if r.status_code != 200:
        print(f"⚠ telegram.sendDocument upload {r.status_code}: {r.text[:200]}")
        return {}
    return r.json()


def send_carousel_preview(
    photo_paths: list[str],
    *,
    caption: str,
    chat_id: str | None = None,
    reply_markup: dict | None = None,
    silent: bool = False,
) -> dict:
    """Envia uma sequência de PNGs como mensagens separadas, simulando um
    carrossel. Os primeiros N-1 slides vão com caption mínima (ex: "slide 1/3").
    O último carrega a caption completa + reply_markup com os botões de
    aprovação. Retorna o response do envio final (o que tem o keyboard) — útil
    pra o handler editar a caption depois pra registrar a decisão.

    Por que não sendMediaGroup (álbum nativo)? Porque a Telegram Bot API não
    aceita inline_keyboard em álbum. Enviar separado é o jeito de manter os
    botões num único ponto de decisão pro carrossel inteiro.
    """
    if not photo_paths:
        return {}
    total = len(photo_paths)
    for i, path in enumerate(photo_paths[:-1], start=1):
        send_photo_file(
            path,
            caption=f"slide {i}/{total}",
            chat_id=chat_id,
            silent=True,  # primeiros slides silenciosos; só o último notifica
        )
    return send_photo_file(
        photo_paths[-1],
        caption=caption,
        chat_id=chat_id,
        reply_markup=reply_markup,
        silent=silent,
    )


def edit_message_text(
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _post("editMessageText", payload)


def edit_message_caption(
    chat_id: int | str,
    message_id: int,
    caption: str,
    *,
    reply_markup: dict | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _post("editMessageCaption", payload)


def answer_callback(callback_query_id: str, text: str = "") -> dict:
    return _post(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text},
    )


def get_updates(offset: int | None, *, timeout: int = 25) -> list[dict]:
    """Long polling. Retorna lista (vazia em erro/timeout)."""
    payload: dict[str, Any] = {
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }
    if offset is not None:
        payload["offset"] = offset
    res = _post("getUpdates", payload, timeout=timeout + 10)
    if not res or not res.get("ok"):
        return []
    return res.get("result", []) or []


def inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Helper: [[(label, callback_data), ...], ...] -> reply_markup dict."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }
