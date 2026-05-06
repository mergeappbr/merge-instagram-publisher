"""
Alertas via Telegram (canal principal) + mirror passivo no Discord (read-only).

Canal isolado da operação Sofia/Oases — usa bot dedicado configurado
nas env vars TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID. Se as vars não
estiverem setadas, o módulo opera silenciosamente (ENABLED=False).

Mirror Discord opcional via DISCORD_WEBHOOK_URL — quando setado, replica
o texto (HTML convertido pra Markdown) num canal Discord. Aprovações e
fotos seguem só no Telegram (Discord é mirror passivo, sem botões).

Falhas no envio NUNCA derrubam o scheduler: notify() retorna False e
loga warning, sem propagar exceção. Falha no Discord não afeta retorno.

Uso:
    from alerts import notify
    notify("<b>Merge</b> · post <code>38</code> publicado")

Mensagens usam HTML mode do Telegram. Strings dinâmicas (post_id,
mensagens de erro, etc) devem ser passadas por html.escape() pelo caller.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import time
from pathlib import Path

import httpx

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ENABLED = bool(BOT_TOKEN and CHAT_ID)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
DISCORD_ENABLED = bool(DISCORD_WEBHOOK_URL)

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 10.0
DISCORD_USERNAME = "Merge Bot"
DISCORD_MAX_LEN = 1900  # margem segura abaixo do limite 2000

# Dedupe: mensagens com mesmo hash não saem 2x dentro dessa janela.
DEDUPE_WINDOW_SECONDS = int(os.getenv("ALERT_DEDUPE_WINDOW_SECONDS", "3600"))
DEDUPE_STATE_PATH = Path(__file__).resolve().parent.parent / "output" / ".alerts_seen.json"
DEDUPE_MAX_ENTRIES = 200


def _html_to_discord_md(text: str) -> str:
    """Converte tags HTML usadas no Telegram pra Markdown do Discord."""
    # Tags com pares
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<strong>(.*?)</strong>", r"**\1**", text, flags=re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<em>(.*?)</em>", r"*\1*", text, flags=re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.DOTALL)
    text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.DOTALL)
    text = re.sub(r"<u>(.*?)</u>", r"__\1__", text, flags=re.DOTALL)
    text = re.sub(r"<s>(.*?)</s>", r"~~\1~~", text, flags=re.DOTALL)
    # Remove tags residuais
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape HTML entities (&lt; → <, &amp; → &, etc)
    text = html_lib.unescape(text)
    return text


def _send_discord(text: str) -> bool:
    if not DISCORD_ENABLED:
        return False
    try:
        body = _html_to_discord_md(text)
        if len(body) > DISCORD_MAX_LEN:
            body = body[: DISCORD_MAX_LEN - 3] + "..."
        r = httpx.post(
            DISCORD_WEBHOOK_URL,
            json={"content": body, "username": DISCORD_USERNAME},
            timeout=TIMEOUT_SECONDS,
        )
        if r.status_code not in (200, 204):
            print(f"⚠ discord {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"⚠ discord exception: {e!r}")
        return False


def _send_telegram(text: str, *, silent: bool) -> bool:
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


def _dedupe_key(text: str) -> str:
    """Hash estável da mensagem (ignora variações triviais de espaço)."""
    norm = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def _load_seen() -> dict[str, float]:
    if not DEDUPE_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(DEDUPE_STATE_PATH.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_seen(seen: dict[str, float]) -> None:
    try:
        DEDUPE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEDUPE_STATE_PATH.write_text(json.dumps(seen), encoding="utf-8")
    except OSError as e:
        print(f"⚠ alerts dedupe save falhou: {e!r}")


def _should_skip(text: str) -> bool:
    """True se mensagem idêntica já saiu dentro de DEDUPE_WINDOW_SECONDS."""
    if DEDUPE_WINDOW_SECONDS <= 0:
        return False
    now = time.time()
    seen = _load_seen()
    # purga entradas velhas
    cutoff = now - DEDUPE_WINDOW_SECONDS
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    key = _dedupe_key(text)
    if key in seen:
        return True
    seen[key] = now
    # cap pra não crescer infinito
    if len(seen) > DEDUPE_MAX_ENTRIES:
        # mantém só as N mais recentes
        items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
        seen = dict(items[:DEDUPE_MAX_ENTRIES])
    _save_seen(seen)
    return False


def notify(text: str, *, silent: bool = False, force: bool = False) -> bool:
    """Manda mensagem (HTML) pro Telegram + mirror Discord (se configurado).

    silent=True suprime notificação sonora no Telegram. Discord ignora silent.
    force=True ignora o dedupe (use só pra resumos diários ou eventos únicos
    que precisam sair mesmo se idênticos a um anterior).

    Dedupe: mensagens idênticas dentro de DEDUPE_WINDOW_SECONDS (default 1h)
    são silenciosamente puladas — evita spam quando um erro repete a cada tick.
    """
    if not force and _should_skip(text):
        return True  # tratamos como sucesso (já entregue antes)
    tg_ok = _send_telegram(text, silent=silent)
    _send_discord(text)  # fire-and-forget, não afeta retorno
    return tg_ok
