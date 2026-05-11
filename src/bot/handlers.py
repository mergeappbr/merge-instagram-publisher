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

    # produce_news não tem approval — `aid` é prefixo do hash do pool.
    # Trata antes do read_approval pra não cair em "expirado".
    if action == "produce_news":
        api.answer_callback(cb_id, "produzindo…")
        _disable_buttons(msg, status="produzindo 🎨")
        try:
            _produce_news_by_hash(aid, chat_id=chat_id)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ produce_news falhou: {e!r}")
            api.send_message(
                f"⚠️ falha ao produzir: {html.escape(str(e)[:200])}",
                chat_id=str(chat_id) if chat_id else None,
            )
        return

    # produce_story: gera arte de story a partir de hash do pool.
    if action == "produce_story":
        api.answer_callback(cb_id, "gerando story…")
        _disable_buttons(msg, status="gerando story 🎨")
        try:
            from news.stories import produce_story_by_hash
            produce_story_by_hash(aid, chat_id=chat_id)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ produce_story falhou: {e!r}")
            api.send_message(
                f"⚠️ falha ao gerar story: {html.escape(str(e)[:200])}",
                chat_id=str(chat_id) if chat_id else None,
            )
        return

    # skip_story: marca item como usado sem renderizar nada.
    if action == "skip_story":
        api.answer_callback(cb_id, "pulado")
        _disable_buttons(msg, status="pulado ⏭️")
        try:
            from news.stories import skip_story_by_hash
            skip_story_by_hash(aid)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ skip_story falhou: {e!r}")
        return

    approval = state.read_approval(aid)
    if approval is None:
        # Fallback: tenta restaurar do R2 (Railway ephemeral fs derruba
        # output/bot_state/pending/ em todo redeploy — sem isso, qualquer
        # push entre dispatch e approval deixa o usuário com "expirado").
        try:
            from bot import r2_persist
            approval = r2_persist.restore_approval(aid)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ r2 restore falhou: {e!r}")
            approval = None
    if approval is None:
        api.answer_callback(cb_id, "expirado ou não encontrado")
        return

    kind = approval.get("kind", "brief")

    if action in ("approve", "publish_now"):
        force_now = action == "publish_now"
        label = "postar agora 🚀" if force_now else "aprovado ✅"
        api.answer_callback(cb_id, label)
        _disable_buttons(msg, status=label)
        fn = HANDLERS_APPROVE.get(kind)
        if fn:
            if force_now:
                # Marca o approval pra on_brief_approve forçar scheduled_at=now
                approval["force_publish_now"] = True
                state.write_approval(approval)
            try:
                fn(approval)
            except Exception as e:  # noqa: BLE001
                print(f"⚠ approve {kind} falhou: {e!r}")
                api.send_message(
                    f"⚠️ falha ao processar aprovação de "
                    f"<code>{html.escape(aid)}</code>: {html.escape(str(e)[:200])}"
                )
                return
        state.archive_approval(
            aid,
            decision="published_now" if force_now else "approved",
        )
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
                "/force_stories — força dispatch de stories (ignora janela)\n"
                "/force_news_feed — força 1 feed news post agora\n"
                "/test_news_feed — testa pipeline com item BR sintético (sem pool)\n"
                "/force_news_fitbit — TEMP: dispara news Fitbit Air vs WHOOP\n"
                "/publish_now [slot] — publica brief news preso na esteira\n"
                "/calendar_news — lista news pendentes no calendar\n"
                "/news_ranking — top 5 news quentes (com botão produzir)"
            )
        elif text == "/pending":
            _list_pending(chat_id)
        elif text == "/races":
            _list_races(chat_id)
        elif text.startswith("/race ") or text.startswith("/race_"):
            # Aceita "/race <id>" e "/race_<id>" (clicando no link da
            # mensagem /races, Telegram manda como underscore).
            sep = " " if text.startswith("/race ") else "_"
            race_id = text[len(f"/race{sep}"):].strip()
            _force_race_countdown(chat_id, race_id)
        elif text == "/news":
            _news_status(chat_id)
        elif text == "/force_news":
            _force_news_watch(chat_id)
        elif text == "/force_stories":
            _force_stories(chat_id)
        elif text == "/force_news_feed":
            _force_news_feed(chat_id)
        elif text == "/test_news_feed":
            _test_news_feed(chat_id)
        elif text == "/force_news_fitbit":
            _force_news_fitbit(chat_id)
        elif text == "/publish_now" or text.startswith("/publish_now "):
            arg = text[len("/publish_now"):].strip()
            _publish_now(chat_id, arg or None)
        elif text == "/calendar_news":
            _list_calendar_news(chat_id)
        elif text == "/news_ranking":
            _send_hourly_news_ranking(chat_id)
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
    msg = (
        f"✅ watcher · novos={stats.get('new',0)} scored={stats.get('scored',0)} "
        f"reactive={stats.get('reactive',0)} pooled={stats.get('pooled',0)}"
    )
    rejected = stats.get("rejected") or []
    if rejected:
        msg += "\n\n<b>rejeitados (score &lt; 5)</b>"
        for r in rejected:
            msg += (
                f"\n· {r['score']:.1f} — <i>{html.escape(r['feed'])}</i> · "
                f"{html.escape(r['title'])}"
            )
            if r.get("reasoning"):
                msg += f"\n  <code>{html.escape(r['reasoning'])}</code>"
    api.send_message(msg, chat_id=str(chat_id))


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


def _force_news_feed(chat_id: int) -> None:
    """Força 1 feed news post agora ignorando janela e state files."""
    api.send_message("⏳ forçando feed news…", chat_id=str(chat_id), silent=True)
    try:
        from news import feed_post
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"⚠️ feed_post indisponível: {html.escape(str(e))}", chat_id=str(chat_id)
        )
        return
    item = feed_post._pick_top_unused()
    if item is None:
        api.send_message(
            f"⚠️ pool sem item score≥{feed_post.MIN_SCORE} pendente. "
            "Rode /force_news pra puxar dos feeds primeiro.",
            chat_id=str(chat_id),
        )
        return
    ok = feed_post.dispatch_one(item, "manual")
    if ok:
        api.send_message("✅ feed news preview enviado.", chat_id=str(chat_id))
    else:
        api.send_message("❌ feed news falhou (veja logs).", chat_id=str(chat_id))


def _test_news_feed(chat_id: int) -> None:
    """Item sintético BR pra testar template + visual.resolve_bg + reviewer.

    Não depende de pool nem MIN_SCORE. Não usa bg_override → exercita
    Pollinations FLUX / Wikipedia. Útil pra validar mudanças sem esperar
    feed real entrar com score alto.
    """
    import hashlib
    from datetime import datetime, timezone
    api.send_message(
        "⏳ teste com item sintético (Maratona POA)…",
        chat_id=str(chat_id), silent=True,
    )
    try:
        from news import feed_post
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"⚠️ feed_post indisponível: {html.escape(str(e))}",
            chat_id=str(chat_id),
        )
        return
    title = (
        "Maratona Olympikus de Porto Alegre 2026 confirma 12 mil inscritos "
        "a 23 dias da prova"
    )
    link = "https://test.merge.example/maratona-poa-2026"
    feed_name = "Merge Test"
    h = hashlib.sha1(("test|" + link + "|" + title).encode("utf-8")).hexdigest()
    item = {
        "feed_name": feed_name,
        "category": "br_running",
        "modalities": ["running"],
        "feed_relevance": 0.9,
        "title": title,
        "link": link,
        "summary": (
            "A Maratona Olympikus de Porto Alegre 2026, no dia 31/05, fecha "
            "12 mil inscritos entre 42K, 21K, 10K e 5K com lotes esgotados. "
            "Faltando 3 semanas pra largada, treinadores brasileiros entram "
            "na fase de taper — redução de volume mantendo intensidade — e "
            "recomendam último longão em ritmo de prova até 14 dias antes. "
            "Provas urbanas no Brasil cresceram 35% em participação amador "
            "desde 2023, segundo a CBAt."
        ),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "hash": h,
        "score": 9.0,
        "post_event": False,
        "viral_potential": 8,
        "alignment": 9,
        "primary_modality": "running",
        "reasoning": "Sintético — testa template + visual.resolve_bg.",
        "angle_suggestion": (
            "Taper a 23 dias da Maratona POA: o que cortar, o que manter e "
            "o último longão em ritmo de prova."
        ),
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = feed_post.dispatch_one(item, "test_synthetic")
    if ok:
        api.send_message("✅ teste enviado — confere o preview.", chat_id=str(chat_id))
    else:
        api.send_message("❌ teste falhou (veja logs Railway).", chat_id=str(chat_id))


def _force_news_fitbit(chat_id: int) -> None:
    """TEMP: dispara news Fitbit Air vs WHOOP (item hardcoded). Remover após uso."""
    import hashlib
    from datetime import datetime, timezone
    api.send_message("⏳ disparando news Fitbit Air vs WHOOP…", chat_id=str(chat_id), silent=True)
    try:
        from news import feed_post
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"⚠️ feed_post indisponível: {html.escape(str(e))}", chat_id=str(chat_id)
        )
        return
    feed_name = "The Verge"
    link = "https://www.theverge.com/2026/5/7/google-fitbit-air-launch-whoop"
    title = "Google launches Fitbit Air at $99 to challenge WHOOP subscription model"
    h = hashlib.sha1((feed_name + "|" + link + "|" + title).encode("utf-8")).hexdigest()
    item = {
        "feed_name": feed_name,
        "category": "tech_wearables",
        "modalities": ["running", "cycling", "wellness"],
        "feed_relevance": 0.7,
        "title": title,
        "link": link,
        "summary": (
            "Google launched the Fitbit Air, a screenless fitness band at US$99.99, "
            "directly targeting WHOOP and other health-monitoring wearables. "
            "Weighs only 5.2g without strap, monitors HR, SpO2, skin temp, has "
            "3-axis accelerometer, 7-day battery. WHOOP 5.0 (closest rival) requires "
            "US$199/year subscription, WHOOP MG plan US$359/year — no hardware-only "
            "option. Fitbit Air works without subscription; optional Health Premium "
            "US$9.99/month or US$79/year. Both track 24h HR, sleep stages, SpO2, "
            "HRV, skin temp, afib detection. WHOOP MG adds FDA-approved ECG. Fitbit "
            "Air gets Google Health Coach (Gemini-powered). Pre-orders May 7, retail "
            "May 26 in US."
        ),
        "published_at": datetime.now(timezone.utc).isoformat(),
        "hash": h,
        "score": 9.0,
        "post_event": False,
        "viral_potential": 9,
        "alignment": 8,
        "primary_modality": "wellness",
        "reasoning": "Lançamento que muda jogo do segmento (sem assinatura vs WHOOP).",
        "angle_suggestion": (
            "Google ataca o WHOOP com Fitbit Air a US$99 — WHOOP deve aparecer em "
            "DESTAQUE na headline (clickbait estratégico, é briga direta entre os "
            "dois produtos). Ex: 'Google ataca o <span class=\"hl\">WHOOP</span> com "
            "Fitbit Air'. Mencionar ambas marcas no caption."
        ),
        "bg_override": "FitbitGoogle.webp",
        "added_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = feed_post.dispatch_one(item, "manual_fitbit")
    if ok:
        api.send_message("✅ Fitbit news preview enviado.", chat_id=str(chat_id))
    else:
        api.send_message("❌ Fitbit news falhou (veja logs).", chat_id=str(chat_id))


def _send_hourly_news_ranking(chat_id: int | None = None) -> int:
    """Lista top news pendentes do pool com botão pra produzir cada uma.

    Filtra: não usado em feed/story, score >= 5. Ordena por score desc.
    Retorna número de items enviados.
    """
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    pool_file = root / "output" / "news_pool.json"
    if not pool_file.exists():
        if chat_id is not None:
            api.send_message("📰 pool vazio.", chat_id=str(chat_id))
        return 0
    try:
        pool = json.loads(pool_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        if chat_id is not None:
            api.send_message("📰 pool corrompido.", chat_id=str(chat_id))
        return 0
    pending = [
        p for p in pool
        if not p.get("used_in_feed")
        and not p.get("used_in_story")
        and float(p.get("score", 0)) >= 5
    ]
    pending.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    top = pending[:5]
    if not top:
        if chat_id is not None:
            api.send_message(
                "📰 nenhuma news quente nesta hora.", chat_id=str(chat_id), silent=True
            )
        return 0

    from datetime import datetime
    header = (
        f"📰 <b>RANKING NEWS · {datetime.now().strftime('%d/%m %Hh')}</b>\n"
        f"<i>top {len(top)} mais quentes do mercado wellness · clica pra produzir</i>"
    )
    api.send_message(header, chat_id=str(chat_id) if chat_id else None, silent=True)

    for i, item in enumerate(top, start=1):
        title = item.get("title", "?")[:160]
        src = item.get("feed_name", "?")
        score = item.get("score", 0)
        modality = item.get("primary_modality", "?")
        angle = item.get("angle_suggestion", "")[:200]
        link = item.get("link", "")
        h = item.get("hash", "")
        msg = (
            f"<b>#{i} · score {score:.1f}</b> · <i>{html.escape(src)}</i> · "
            f"<code>{html.escape(modality)}</code>\n"
            f"<b>{html.escape(title)}</b>"
        )
        if angle:
            msg += f"\n💡 <i>{html.escape(angle)}</i>"
        if link:
            msg += f"\n🔗 {html.escape(link)}"
        kb = api.inline_keyboard([
            [("🎨 Produzir", f"produce_news:{h[:32]}")],
        ])
        api.send_message(
            msg,
            chat_id=str(chat_id) if chat_id else None,
            reply_markup=kb,
            silent=True,
        )
    return len(top)


def _produce_news_by_hash(hash_prefix: str, chat_id: int | None = None) -> bool:
    """Acha item no pool por prefixo de hash e dispara feed_post.dispatch_one."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    pool_file = root / "output" / "news_pool.json"
    if not pool_file.exists():
        api.send_message("📰 pool não existe.", chat_id=str(chat_id) if chat_id else None)
        return False
    pool = json.loads(pool_file.read_text(encoding="utf-8"))
    item = next((p for p in pool if (p.get("hash") or "").startswith(hash_prefix)), None)
    if item is None:
        api.send_message(
            f"⚠️ item <code>{html.escape(hash_prefix)}</code> não está no pool.",
            chat_id=str(chat_id) if chat_id else None,
        )
        return False
    try:
        from news import feed_post
    except Exception as e:  # noqa: BLE001
        api.send_message(
            f"⚠️ feed_post indisponível: {html.escape(str(e))}",
            chat_id=str(chat_id) if chat_id else None,
        )
        return False
    api.send_message(
        f"⏳ produzindo: <i>{html.escape(item.get('title','?')[:80])}</i>",
        chat_id=str(chat_id) if chat_id else None,
        silent=True,
    )
    return feed_post.dispatch_one(item, "ranking_pick")


def _list_pending_rows(themes: tuple[str, ...] | None = None) -> list[dict]:
    """Lê calendar.csv e retorna rows ainda não publicadas. Filtra por themes
    se fornecido (ex: ('news',) ou ('news','endurance','ironman'))."""
    import csv
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    cal = root / "content" / "calendar.csv"
    if not cal.exists():
        return []
    from scheduler import published_post_ids, load_skipped, _norm
    done = published_post_ids()
    skipped = load_skipped()
    with cal.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    pending = []
    for r in rows:
        theme = (r.get("theme") or "").strip()
        if themes is not None and theme not in themes:
            continue
        nid = _norm(r.get("post_id", ""))
        if nid in done or nid in skipped:
            continue
        pending.append(r)
    return pending


def _list_pending_news_rows() -> list[dict]:
    """Compat: news pendentes (usado por /calendar_news)."""
    return _list_pending_rows(themes=("news",))


def _list_calendar_news(chat_id: int) -> None:
    """Lista news posts pendentes no calendar — útil pra escolher slot do /publish_now."""
    pending = _list_pending_news_rows()
    if not pending:
        api.send_message("📅 nenhuma news pendente no calendar.", chat_id=str(chat_id))
        return
    lines = ["📰 <b>news pendentes no calendar</b>", ""]
    for r in pending[:20]:
        lines.append(
            f"slot <code>{html.escape(r['slot'])}</code> · "
            f"{html.escape(r['scheduled_at'])} · "
            f"<code>{html.escape(r['post_id'][:50])}</code>"
        )
    lines.append("")
    lines.append("usa <code>/publish_now &lt;slot&gt;</code> pra publicar agora.")
    api.send_message("\n".join(lines), chat_id=str(chat_id))


def _publish_now(chat_id: int, arg: str | None) -> None:
    """Reposiciona scheduled_at de uma row pendente.

    - Rows com theme=endurance/ironman (countdown de prova): vai pro próximo
      HH:00 livre HOJE (9h-21h). Posts T-30/T-15/T-7/T-1 são sensíveis a
      janela do dia, não entram na esteira regular.
    - Demais rows (news, etc): vai pra agora — próximo tick publica.

    Sem args: se exatamente 1 row pendente (news+countdown), move ela.
    Com slot/post_id: move aquele específico.
    """
    import csv
    from datetime import datetime
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    cal = root / "content" / "calendar.csv"
    pending = _list_pending_rows(themes=("news", "endurance", "ironman"))
    if not pending:
        api.send_message("📅 nenhuma row pendente (news/countdown).", chat_id=str(chat_id))
        return
    target = None
    if arg:
        for r in pending:
            if r["slot"] == arg or r["post_id"] == arg:
                target = r
                break
        if target is None:
            api.send_message(
                f"⚠️ slot/id <code>{html.escape(arg)}</code> não está em pendentes. "
                f"Rode <code>/calendar_news</code>.",
                chat_id=str(chat_id),
            )
            return
    else:
        if len(pending) > 1:
            api.send_message(
                f"⚠️ {len(pending)} rows pendentes. Use <code>/calendar_news</code> "
                f"e depois <code>/publish_now &lt;slot|post_id&gt;</code>.",
                chat_id=str(chat_id),
            )
            return
        target = pending[0]

    # Decide nova data: countdown → próximo HH:00 livre hoje; news → agora.
    theme = (target.get("theme") or "").strip()
    is_countdown = theme in ("endurance", "ironman")
    if is_countdown:
        from autogen import calendar_io
        new_dt = calendar_io.next_free_round_hour()
        mode_label = "próximo HH:00 livre"
    else:
        new_dt = datetime.now()
        mode_label = "agora (próximo tick)"
    new_when = new_dt.strftime("%Y-%m-%d %H:%M")

    with cal.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = rows[0].keys() if rows else []
    for r in rows:
        if r["slot"] == target["slot"] and r["post_id"] == target["post_id"]:
            r["scheduled_at"] = new_when
            break
    with cal.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    api.send_message(
        f"🚀 slot <code>{html.escape(target['slot'])}</code> "
        f"(<code>{html.escape(target['post_id'][:40])}</code> · {html.escape(theme)}) "
        f"reposicionado: <code>{html.escape(new_when)}</code> ({mode_label}).",
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
