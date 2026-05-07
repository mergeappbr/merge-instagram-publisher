"""
Handlers de updates do Telegram (callback_query + texto livre).

Tipos de approval suportados:
  - "brief"   → aprovação de brief autogen ou reativo (vai pro calendar)
  - "story"   → aprovação de stories de notícia (publica imediato no IG)

Callback_data formato: "<action>:<approval_id>"
  Actions: approve, reject, adjust, publish, skip, next

Quando user clica "adjust", marcamos o chat como awaiting; próximo texto vira
instrução de regeneração e re-envia preview novo.
"""
from __future__ import annotations

import html
from typing import Callable

from . import api, state

# Callbacks de regeneração / publish são injetados pelos módulos donos
# (autogen.runner, news.reactive, news.stories) pra evitar import circular.
HANDLERS_REGEN: dict[str, Callable[[dict, str], None]] = {}
HANDLERS_APPROVE: dict[str, Callable[[dict], None]] = {}
HANDLERS_REJECT: dict[str, Callable[[dict], None]] = {}


def register_kind(
    kind: str,
    *,
    on_approve: Callable[[dict], None],
    on_reject: Callable[[dict], None],
    on_regen: Callable[[dict, str], None] | None = None,
) -> None:
    """Registra callbacks pra um tipo de approval. Chamado no startup do bot."""
    HANDLERS_APPROVE[kind] = on_approve
    HANDLERS_REJECT[kind] = on_reject
    if on_regen is not None:
        HANDLERS_REGEN[kind] = on_regen


def handle_update(upd: dict) -> None:
    if "callback_query" in upd:
        _handle_callback(upd["callback_query"])
    elif "message" in upd:
        _handle_message(upd["message"])


def _handle_callback(cb: dict) -> None:
    cb_id = cb.get("id", "")
    data = cb.get("data", "") or ""
    msg = cb.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")

    if ":" not in data:
        api.answer_callback(cb_id, "comando inválido")
        return
    action, aid = data.split(":", 1)

    approval = state.read_approval(aid)
    if approval is None:
        api.answer_callback(cb_id, "expirado ou não encontrado")
        return

    kind = approval.get("kind", "brief")

    if action == "approve":
        api.answer_callback(cb_id, "aprovado")
        _disable_buttons(msg, status="aprovado ✅")
        fn = HANDLERS_APPROVE.get(kind)
        if fn:
            try:
                fn(approval)
            except Exception as e:  # noqa: BLE001
                print(f"⚠ approve {kind} falhou: {e!r}")
                api.send_message(
                    f"⚠️ falha ao processar aprovação de "
                    f"<code>{html.escape(aid)}</code>: {html.escape(str(e)[:200])}"
                )
                return
        state.archive_approval(aid, decision="approved")
        return

    if action == "reject":
        api.answer_callback(cb_id, "rejeitado")
        _disable_buttons(msg, status="rejeitado ❌")
        fn = HANDLERS_REJECT.get(kind)
        if fn:
            try:
                fn(approval)
            except Exception as e:  # noqa: BLE001
                print(f"⚠ reject {kind} falhou: {e!r}")
        state.archive_approval(aid, decision="rejected")
        return

    if action == "adjust":
        api.answer_callback(cb_id, "ok, aguardando texto")
        if chat_id is not None:
            state.set_awaiting(chat_id, aid, kind="adjust")
            api.send_message(
                "✏️ <b>Escreve o que mudar.</b>\n"
                "ex: <i>“deixa a legenda mais curta”</i>, "
                "<i>“troca a headline”</i>, "
                "<i>“foca no fator altitude”</i>",
                reply_to=msg.get("message_id"),
            )
        return

    api.answer_callback(cb_id, "ação desconhecida")


def _handle_message(msg: dict) -> None:
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not text or chat_id is None:
        return

    awaiting = state.get_awaiting(chat_id)
    if awaiting is None:
        # Comando direto — responde só se for /start ou similar; resto ignora.
        if text in ("/start", "/help"):
            api.send_message(
                "Bot da <b>Merge</b>. Recebe previews e aceita ajustes em texto livre.\n\n"
                "<b>Comandos</b>\n"
                "/pending — lista approvals pendentes\n"
                "/races — lista provas configuradas\n"
                "/race &lt;id&gt; — força countdown agora\n"
                "/news — status do news watcher + pool\n"
                "/force_news — força watcher rodar agora\n"
                "/force_stories — força dispatch de stories (ignora janela)"
            )
        elif text == "/pending":
            _list_pending(chat_id)
        elif text == "/races":
            _list_races(chat_id)
        elif text.startswith("/race "):
            race_id = text[len("/race "):].strip()
            _force_race_countdown(chat_id, race_id)
        elif text == "/news":
            _news_status(chat_id)
        elif text == "/force_news":
            _force_news_watch(chat_id)
        elif text == "/force_stories":
            _force_stories(chat_id)
        return

    # Texto livre direcionado a um approval específico
    aid = awaiting.get("approval_id", "")
    approval = state.read_approval(aid)
    if approval is None:
        state.clear_awaiting(chat_id)
        api.send_message("⚠️ approval não encontrado (provavelmente expirou).")
        return

    kind = approval.get("kind", "brief")
    fn = HANDLERS_REGEN.get(kind)
    state.clear_awaiting(chat_id)
    if fn is None:
        api.send_message("⚠️ tipo não suporta ajuste.")
        return
    api.send_message(
        f"⏳ regenerando com instrução: <i>{html.escape(text[:200])}</i>",
        silent=True,
    )
    try:
        fn(approval, text)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ regen {kind} falhou: {e!r}")
        api.send_message(
            f"❌ falha ao regenerar: <code>{html.escape(str(e)[:300])}</code>"
        )


def _disable_buttons(msg: dict, *, status: str) -> None:
    """Edita o caption/text do preview pra registrar a decisão e remover botões."""
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    msg_id = msg.get("message_id")
    if chat_id is None or msg_id is None:
        return

    if "caption" in msg:
        new_cap = (msg.get("caption") or "") + f"\n\n— <b>{status}</b>"
        api.edit_message_caption(chat_id, msg_id, new_cap[:1024], reply_markup={"inline_keyboard": []})
    elif "text" in msg:
        new_text = (msg.get("text") or "") + f"\n\n— <b>{status}</b>"
        api.edit_message_text(chat_id, msg_id, new_text[:4096], reply_markup={"inline_keyboard": []})


def _list_races(chat_id: int) -> None:
    try:
        from ironman import config as race_cfg
        from ironman import config as cfg_mod  # noqa: F401
    except Exception as e:  # noqa: BLE001
        api.send_message(f"⚠️ módulo ironman indisponível: {html.escape(str(e))}", chat_id=str(chat_id))
        return
    from datetime import datetime
    races = race_cfg.load_races()
    if not races:
        api.send_message("nenhuma prova em races.yml.", chat_id=str(chat_id))
        return
    today = datetime.now().date()
    lines = [f"<b>{len(races)} prova(s) configurada(s)</b>"]
    for r in races:
        d = race_cfg.days_until(r, today)
        when = f"T-{d}" if d > 0 else (f"T+{-d}" if d < 0 else "hoje")
        lines.append(
            f"· <code>{html.escape(r['id'])}</code> · {html.escape(r.get('kind','?'))} · "
            f"{html.escape(r.get('name','?'))} · {when}"
        )
    api.send_message("\n".join(lines), chat_id=str(chat_id))


def _force_race_countdown(chat_id: int, race_id: str) -> None:
    if not race_id:
        api.send_message(
            "uso: <code>/race &lt;id&gt;</code>\nveja /races pra IDs.",
            chat_id=str(chat_id),
        )
        return
    try:
        from ironman import config as race_cfg
        from ironman import runner as race_runner
    except Exception as e:  # noqa: BLE001
        api.send_message(f"⚠️ ironman indisponível: {html.escape(str(e))}", chat_id=str(chat_id))
        return
    from datetime import datetime
    races = race_cfg.load_races()
    race = next((r for r in races if r["id"] == race_id), None)
    if race is None:
        api.send_message(
            f"⚠️ race <code>{html.escape(race_id)}</code> não encontrada. veja /races.",
            chat_id=str(chat_id),
        )
        return
    today = datetime.now().date()
    days = race_cfg.days_until(race, today)
    if days <= 0:
        api.send_message(
            f"⚠️ <code>{html.escape(race_id)}</code> já aconteceu (d_until={days}).",
            chat_id=str(chat_id),
        )
        return
    api.send_message(
        f"⏳ gerando countdown forçado · <code>{html.escape(race_id)}</code> · T-{days}",
        chat_id=str(chat_id),
        silent=True,
    )
    try:
        ok = race_runner.dispatch_countdown(race, days)
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"❌ falha: <code>{html.escape(str(e)[:300])}</code>",
            chat_id=str(chat_id),
        )
        return
    if not ok:
        api.send_message("❌ render falhou (veja logs).", chat_id=str(chat_id))


def _news_status(chat_id: int) -> None:
    """Resume estado do news watcher e pool: última rodada, total no pool,
    top 5 items não usados, e quando os slots de stories rodaram pela última vez."""
    import json as _json
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent.parent
    pool_file = root / "output" / "news_pool.json"
    watcher_state = root / "output" / ".last_news_watcher.txt"
    morning_state = root / "output" / ".last_stories_morning.txt"
    afternoon_state = root / "output" / ".last_stories_afternoon.txt"

    def _read(p):
        try:
            return p.read_text(encoding="utf-8").strip() if p.exists() else "—"
        except OSError:
            return "ERR"

    pool: list[dict] = []
    if pool_file.exists():
        try:
            pool = _json.loads(pool_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pool = []
    pending = [p for p in pool if not p.get("used_in_story")]
    pending.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

    lines = [
        "<b>📰 News status</b>",
        f"watcher último run: <code>{html.escape(_read(watcher_state))}</code>",
        f"stories manhã: <code>{html.escape(_read(morning_state))}</code>",
        f"stories tarde: <code>{html.escape(_read(afternoon_state))}</code>",
        "",
        f"pool total: {len(pool)} · pendentes: {len(pending)}",
    ]
    if pending:
        lines.append("")
        lines.append("<b>top 5 pendentes</b>")
        for it in pending[:5]:
            sc = it.get("score", "?")
            ti = (it.get("title") or "?")[:90]
            fn = it.get("feed_name", "?")
            lines.append(f"· [{sc}] {html.escape(fn)} · {html.escape(ti)}")
    api.send_message("\n".join(lines), chat_id=str(chat_id))


def _force_news_watch(chat_id: int) -> None:
    api.send_message("⏳ rodando news watcher…", chat_id=str(chat_id), silent=True)
    try:
        from news.watcher import watch_once
    except Exception as e:  # noqa: BLE001
        api.send_message(f"⚠️ news indisponível: {html.escape(str(e))}", chat_id=str(chat_id))
        return
    try:
        stats = watch_once()
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"❌ watch_once falhou: <code>{html.escape(str(e)[:300])}</code>",
            chat_id=str(chat_id),
        )
        return
    api.send_message(
        f"✅ watcher · novos={stats.get('new',0)} scored={stats.get('scored',0)} "
        f"reactive={stats.get('reactive',0)} pooled={stats.get('pooled',0)}",
        chat_id=str(chat_id),
    )


def _force_stories(chat_id: int) -> None:
    """Força dispatch de stories ignorando janela de horário e state files.
    Usa direto a parte interna do maybe_dispatch."""
    api.send_message("⏳ forçando dispatch de stories…", chat_id=str(chat_id), silent=True)
    try:
        from news import stories as st
    except Exception as e:  # noqa: BLE001
        api.send_message(f"⚠️ stories indisponível: {html.escape(str(e))}", chat_id=str(chat_id))
        return
    items = st._next_unused_from_pool(st.STORIES_PER_RUN)
    if not items:
        api.send_message(
            "⚠️ pool sem items pendentes (score≥5). "
            "Rode /force_news pra puxar dos feeds primeiro.",
            chat_id=str(chat_id),
        )
        return
    sent = 0
    for item in items:
        try:
            brief = st._make_story_brief(item)
            st._save_brief_json(brief)
            if not st._render_story_only(brief["id"]):
                continue
            aid = st._create_approval(brief, item)
            st._preview_story(brief, aid, item)
            sent += 1
        except Exception as e:  # noqa: BLE001
            api.send_message(
                f"⚠️ story falhou: <code>{html.escape(str(e)[:200])}</code>",
                chat_id=str(chat_id),
            )
    api.send_message(
        f"✅ dispatch forçado: {sent} preview(s) enviado(s).",
        chat_id=str(chat_id),
    )


def _list_pending(chat_id: int) -> None:
    items = state.list_pending()
    if not items:
        api.send_message("nenhum approval pendente.", chat_id=str(chat_id))
        return
    lines = [f"<b>{len(items)} approval(s) pendente(s)</b>"]
    for it in items[:20]:
        kind = it.get("kind", "?")
        title = it.get("title", "—")
        aid = it.get("id", "?")
        lines.append(
            f"· <code>{html.escape(aid)}</code> · {html.escape(kind)} · "
            f"{html.escape(title[:60])}"
        )
    api.send_message("\n".join(lines), chat_id=str(chat_id))
