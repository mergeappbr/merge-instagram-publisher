"""
Long-polling do bot Telegram da Merge.

Service dedicado no Railway (entrypoint do `bot:` no Procfile). Roda eternamente
fazendo getUpdates com timeout=25s e despachando pra handlers.

Registra os tipos de approval logo no boot:
  - "brief"  → autogen.runner
  - "story"  → news.stories  (registrado quando o módulo estiver disponível)

Falhas em handle_update são logadas mas NUNCA derrubam o loop.
"""
from __future__ import annotations

import sys
import time
import traceback

# Permite imports do tipo `from alerts import notify` quando rodando como módulo
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alerts import notify  # noqa: E402
from bot import api, handlers, state  # noqa: E402

POLL_TIMEOUT = 25  # segundos no getUpdates
ERROR_BACKOFF = 5


def _register_handlers() -> None:
    """Registra callbacks por kind. Imports tardios pra evitar ciclo."""
    try:
        from autogen.runner import on_brief_approve, on_brief_reject, on_brief_regen
        handlers.register_kind(
            "brief",
            on_approve=on_brief_approve,
            on_reject=on_brief_reject,
            on_regen=on_brief_regen,
        )
        print("bot · registrado handler 'brief'")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ não foi possível registrar 'brief': {e!r}")

    try:
        from news.stories import on_story_approve, on_story_reject
        handlers.register_kind(
            "story",
            on_approve=on_story_approve,
            on_reject=on_story_reject,
            on_regen=None,  # stories não suportam ajuste — fluxo rápido
        )
        print("bot · registrado handler 'story'")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ não foi possível registrar 'story': {e!r}")


def main() -> None:
    if not api.BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN ausente")
    print(f"Merge bot · long polling (timeout={POLL_TIMEOUT}s)")
    _register_handlers()

    notify("🤖 <b>Merge bot</b> online (approvals + ajustes via texto livre)", silent=True)

    offset = state.get_offset()
    if offset is not None:
        offset += 1  # próximo update

    while True:
        try:
            updates = api.get_updates(offset, timeout=POLL_TIMEOUT)
            for upd in updates:
                upd_id = upd.get("update_id")
                if upd_id is None:
                    continue
                try:
                    handlers.handle_update(upd)
                except Exception as e:  # noqa: BLE001
                    print(f"⚠ handle_update falhou (update_id={upd_id}): {e!r}")
                    traceback.print_exc()
                offset = upd_id + 1
                state.set_offset(upd_id)
        except KeyboardInterrupt:
            print("bot · encerrando")
            return
        except Exception as e:  # noqa: BLE001
            print(f"⚠ poller exception: {e!r}")
            traceback.print_exc()
            time.sleep(ERROR_BACKOFF)


if __name__ == "__main__":
    main()
