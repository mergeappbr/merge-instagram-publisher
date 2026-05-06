"""
Alertas via Telegram para o worker do Merge.

Canal isolado da operação Sofia/Oases — usa bot dedicado configurado
nas env vars TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID. Se as vars não
estiverem setadas, o módulo opera silenciosamente (ENABLED=False).

Falhas no envio NUNCA derrubam o scheduler: notify() retorna False e
loga warning, sem propagar exceção.

Uso:
    from alerts import notify
    notify("<b>Merge</b> · post <code>38</code> publicado")

Mensagens usam HTML mode do Telegram. Strings dinâmicas (post_id,
mensagens de erro, etc) devem ser passadas por html.escape() pelo caller.
"""
from __future__ import annotations

import os

import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ENABLED = bool(BOT_TOKEN and CHAT_ID)

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 10.0


def notify(text: str, *, silent: bool = False) -> bool:
    """Manda mensagem (HTML) pro chat configurado.

    silent=True suprime notificação sonora (mensagem chega calada). Útil
    pra logs rotineiros tipo boot do worker e resumo diário.
    """
    if not ENABLED:
        return False
    try:
        r = httpx.post(
            f"{API_BASE}/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": silent,
                "disable_web_page_preview": True,
            },
            timeout=TIMEOUT_SECONDS,
        )
        if r.status_code != 200:
            print(f"⚠ telegram {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠ telegram exception: {e!r}")
        return False
