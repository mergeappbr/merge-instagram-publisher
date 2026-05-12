"""News feed post 2x/dia (08h/14h BRT) — acompanha stories.

Pega o TOP item do news_pool (não usado), roda pelo writer+reviewer do
autogen (caption longa, anti claim/anti emoji), gera PNG, manda preview
Telegram. Approval reusa kind='brief' do autogen — fluxo idêntico ao
reactive, sem cooldown 24h porque é programado.

Coordenação com stories: pra evitar overlap, stories pula o item top-1
(que é reservado pra feed). feed_post.py marca `used_in_feed` no dispatch
(não na aprovação) pra stories não pegar o mesmo item depois.

State files (separados de stories):
  output/.last_feed_news_morning.txt
  output/.last_feed_news_afternoon.txt
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from alerts import notify
from autogen import lang_guard, reviewer, runner as autogen_runner, writer
from bot import api, r2_persist, state as bot_state
from news import visual

ROOT = Path(__file__).resolve().parent.parent.parent
POOL_FILE = ROOT / "output" / "news_pool.json"
STATE_MORNING = ROOT / "output" / ".last_feed_news_morning.txt"
STATE_AFTERNOON = ROOT / "output" / ".last_feed_news_afternoon.txt"

MORNING_HOUR = 8
AFTERNOON_HOUR = 14
MIN_SCORE = 6.0  # bar mais alta que stories (5.0) — feed exige mais qualidade


def _slug(s: str, max_len: int = 30) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s[:max_len] or "news"


def _load_pool() -> list[dict]:
    if not POOL_FILE.exists():
        return []
    try:
        return json.loads(POOL_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_pool(pool: list[dict]) -> None:
    POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    POOL_FILE.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def _pick_top_unused() -> dict | None:
    pool = _load_pool()
    pending = [
        p for p in pool
        if not p.get("used_in_feed")
        and not p.get("used_in_story")
        and float(p.get("score", 0)) >= MIN_SCORE
    ]
    if not pending:
        return None
    pending.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return pending[0]


def _pick_top_unused_n(n: int) -> list[dict]:
    """Top-N candidatos pra shortlist (não rende, só lista)."""
    pool = _load_pool()
    pending = [
        p for p in pool
        if not p.get("used_in_feed")
        and not p.get("used_in_story")
        and float(p.get("score", 0)) >= MIN_SCORE
    ]
    pending.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return pending[:n]


def _mark_used_in_feed(item_hash: str) -> None:
    pool = _load_pool()
    for p in pool:
        if p.get("hash") == item_hash:
            p["used_in_feed"] = True
            p["used_at_feed"] = datetime.now().isoformat(timespec="seconds")
    _save_pool(pool)


def _slot_state_file(now: datetime) -> Path | None:
    """Mesma lógica catch-up de stories: depois das 8/14h, dispara se ainda
    não rodou hoje. Tarde tem prioridade."""
    today = now.date().isoformat()

    def _done(p: Path) -> bool:
        return p.exists() and p.read_text(encoding="utf-8").strip() == today

    if now.hour >= AFTERNOON_HOUR and not _done(STATE_AFTERNOON):
        return STATE_AFTERNOON
    if now.hour >= MORNING_HOUR and not _done(STATE_MORNING):
        return STATE_MORNING
    return None


def _next_slot_iso() -> str:
    from autogen import calendar_io
    slot = calendar_io.next_free_slots(1)[0]
    return slot.strftime("%Y-%m-%d %H:%M")


def _build_brief(item: dict, slot_label: str) -> tuple[dict, dict, dict]:
    """Roda writer + reviewer pra item de news. Retorna (brief, plan_entry, review)."""
    bid = f"news_{slot_label}_{datetime.now().strftime('%Y%m%d_%H%M')}_{_slug(item.get('title','news'), 20)}"
    plan_entry = {
        "scheduled_at": _next_slot_iso(),
        "slot_hour": datetime.now().hour,
        "theme": "news",
        "modality": item.get("primary_modality") or (item.get("modalities") or ["wellness"])[0],
        "format": "static",
        "template": "stat" if item.get("post_event") else "feature",
        "pillar": "news-scheduled",
        "hook_idea": item.get("angle_suggestion") or item.get("title", ""),
        "lead_idea": item.get("summary", "")[:300],
        "caption_angle": item.get("angle_suggestion") or item.get("title", ""),
        "is_wildcard": False,
    }
    news_context = {
        "title": item.get("title", ""),
        "source": item.get("feed_name", ""),
        "url": item.get("link", ""),
        "summary": item.get("summary", ""),
        "post_event": item.get("post_event", False),
        "modality": plan_entry["modality"],
    }
    brief = writer.write_brief(plan_entry, news_context=news_context)
    brief["id"] = bid
    # Language guard: writer ocasionalmente devolve trechos em inglês quando
    # a fonte é inglesa. Detecta + traduz via Gemini Flash. Fail-silent.
    try:
        lang_guard.ensure_portuguese(brief)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ lang_guard.ensure_portuguese erro: {e!r}")
    # Override de BG — quando o item traz `bg_override`, força no brief (e no
    # story_vars) sobrescrevendo o que o writer escolheu. Útil pra notícias
    # com foto oficial dedicada (ex: lançamento Fitbit Air).
    bg_override = item.get("bg_override")
    bg_source = "writer-default"  # debug label exibido no preview
    bg_final = brief.get("vars", {}).get("BG_IMAGE", "—")
    if bg_override:
        brief.setdefault("vars", {})["BG_IMAGE"] = bg_override
        if "story_vars" in brief:
            brief["story_vars"]["BG_IMAGE"] = bg_override
        bg_source = "override"
        bg_final = bg_override
    else:
        # Sem override → resolve bg inteligente (Wikipedia entity OR FLUX scene).
        # Falha silenciosa: mantém o que writer escolheu de bg_pool.
        bg_url = visual.resolve_bg_for_news(
            aid=bid,
            title=news_context["title"],
            summary=news_context["summary"],
            modality=plan_entry["modality"],
        )
        if bg_url:
            brief.setdefault("vars", {})["BG_IMAGE"] = bg_url
            if "story_vars" in brief:
                brief["story_vars"]["BG_IMAGE"] = bg_url
            bg_source = "visual.resolve_bg"
            bg_final = bg_url
        else:
            bg_source = "visual.resolve_bg→None (fallback writer)"
    brief["_bg_debug"] = {"source": bg_source, "final": bg_final}

    # NEWS sempre usa o template magazine (foto full-bleed + watermark merge.
    # no topo + headline gigante no rodapé). Override pós-writer pra não
    # depender do LLM escolher template certo.
    brief["template"] = "news_magazine"
    vars_ = brief.setdefault("vars", {})
    vars_["SOURCE"] = (item.get("feed_name") or news_context["source"] or "—").strip()
    if "story_vars" in brief:
        sv = brief["story_vars"]
        sv.setdefault("SOURCE", vars_["SOURCE"])

    # Carrossel multi-foto: quando asset_finder achar 2+ fotos validadas do
    # mesmo produto, vira post de carrossel. Slide 1 = news_magazine atual
    # (capa com headline + LEAD, usando a foto hero como BG). Slides 2-N =
    # news_photo (foto limpa + watermark merge. canto inferior direito).
    # Sem 2ª foto válida → segue post estático. Falha silenciosa.
    multi_n = 0
    multi_err = ""
    try:
        from news import asset_finder
        multi = asset_finder.find_official_images_multi(
            news_context["title"], news_context["summary"], max_n=5
        )
        multi_n = len(multi.get("photos") or []) if multi else 0
    except Exception as e:  # noqa: BLE001
        print(f"⚠ asset_finder.find_official_images_multi erro: {e!r}")
        multi = None
        multi_err = type(e).__name__
    brief["_bg_debug"]["multi_n"] = multi_n
    if multi_err:
        brief["_bg_debug"]["multi_err"] = multi_err
    if multi and multi_n >= 2:
        photos = multi["photos"]
        # Hero (slide 1) — prioriza shot_type=hero, senão a 1ª
        hero_idx = 0
        for i, p in enumerate(photos):
            vision = p.get("vision") or {}
            if vision.get("shot_type") == "hero":
                hero_idx = i
                break
        hero = photos[hero_idx]
        hero_uri = Path(hero["path"]).as_uri()
        # Força hero como BG do slide 1
        vars_["BG_IMAGE"] = hero_uri
        if "story_vars" in brief:
            brief["story_vars"]["BG_IMAGE"] = hero_uri
        brief["_bg_debug"]["source"] = "asset_finder.multi (carousel)"
        brief["_bg_debug"]["final"] = hero_uri
        # Slides 2-N — restantes (preservando ordem do asset_finder)
        extras = [Path(p["path"]).as_uri() for i, p in enumerate(photos) if i != hero_idx]
        brief["extra_photos"] = extras
        print(f"📸 carrossel news: 1 capa + {len(extras)} slide(s) foto")

    review = reviewer.review(brief)
    return brief, plan_entry, review


def _send_preview(
    brief: dict, plan_entry: dict, review: dict, approval_id: str, item: dict, slot_label: str
) -> None:
    feed_png = ROOT / "output" / "feed" / f"{brief['id']}.png"
    if not feed_png.exists():
        notify(f"⚠️ feed news render não gerou {feed_png.name}")
        return

    vars_ = brief.get("vars", {})
    head_plain = re.sub(r"<[^>]+>", "", vars_.get("HEADLINE", ""))
    lead_plain = (
        re.sub(r"<[^>]+>", "", vars_.get("LEAD", ""))
        .replace("<br>", "\n").replace("<br/>", "\n")
    )
    caption = brief.get("caption_md", "")
    score = item.get("score", "?")

    warns = review.get("warnings", [])
    blockers = review.get("blockers", [])
    review_block = ""
    if warns or blockers:
        bullets = [f"❗ {b}" for b in blockers] + [f"⚠️ {w}" for w in warns]
        review_block = "\n\n<b>review</b>\n" + "\n".join(bullets[:6])

    # Photo caption: só metadata curta (ARTE + score). Legenda completa vai
    # numa mensagem de texto separada logo depois (Telegram caption tem cap
    # de 1024, texto vai até 4096 — caption_md inteira cabe sem truncar).
    bg_dbg = brief.get("_bg_debug") or {}
    bg_source = bg_dbg.get("source", "—")
    bg_final = str(bg_dbg.get("final", "—"))
    # Esquema da URL pra diagnóstico (file/https/data/none)
    if bg_final.startswith("file://"):
        bg_scheme = "file://"
    elif bg_final.startswith("https://"):
        bg_scheme = "https://"
    elif bg_final.startswith("http://"):
        bg_scheme = "http://"
    elif bg_final.startswith("data:"):
        bg_scheme = "data:"
    elif bg_final in ("—", ""):
        bg_scheme = "—"
    else:
        bg_scheme = "slug"
    bg_final_short = bg_final.split("/")[-1][:60] if bg_final else "—"
    bg_source = f"{bg_source} [{bg_scheme}]"
    multi_n = bg_dbg.get("multi_n", 0)
    multi_err = bg_dbg.get("multi_err", "")
    if multi_n >= 2:
        multi_line = f"📸 carrossel · {multi_n} fotos via asset_finder"
    elif multi_n == 1:
        multi_line = "📸 estático · asset_finder achou só 1 foto (carrossel precisa ≥2)"
    else:
        multi_line = f"📸 estático · asset_finder=0 fotos{f' ({multi_err})' if multi_err else ''}"
    cap_lines = [
        f"📰 <b>FEED NEWS · {slot_label.upper()} · score {score}</b>",
        f"<i>{html.escape(item.get('feed_name','?'))}</i>",
        f"→ {html.escape(item.get('title','')[:120])}",
        "",
        f"sched: {html.escape(plan_entry.get('scheduled_at','?'))} · "
        f"template {html.escape(brief.get('template','?'))}",
        f"BG: <code>{html.escape(bg_source)}</code> → "
        f"<code>{html.escape(bg_final_short)}</code>",
        multi_line,
        "",
        f"<b>ARTE</b>",
        f"HEAD: {html.escape(head_plain)[:200]}",
        f"LEAD: {html.escape(lead_plain)[:300]}",
    ]
    photo_caption = "\n".join(cap_lines)
    if len(photo_caption) > 1024:
        photo_caption = photo_caption[:1020] + "..."

    # Slides do carrossel (capa + extras .2.png .. .N.png). publish.py vai
    # subir tudo nessa ordem. Manda TODOS os slides como document pra revisão.
    feed_dir = feed_png.parent
    bid = brief["id"]
    slides = [feed_png]
    i = 2
    while True:
        extra = feed_dir / f"{bid}.{i}.png"
        if not extra.exists():
            break
        slides.append(extra)
        i += 1
    total = len(slides)
    # Capa: caption completa de metadata. Demais: só "slide N/M" silencioso.
    for idx, slide_path in enumerate(slides, start=1):
        if idx == 1:
            api.send_document_file(str(slide_path), caption=photo_caption)
        else:
            api.send_document_file(
                str(slide_path),
                caption=f"slide {idx}/{total}",
                silent=True,
            )

    # Mensagem 2: legenda completa (cap_md) + review + botões
    cap_text_lines = [f"<b>LEGENDA COMPLETA</b> ({len(caption)} chars)", ""]
    # Telegram text msg cap 4096; deixamos margem pra review_block + id
    cap_text_lines.append(f"<pre>{html.escape(caption[:3500])}</pre>")
    if len(caption) > 3500:
        cap_text_lines.append(f"<i>... +{len(caption)-3500} chars</i>")
    if review_block:
        cap_text_lines.append(review_block)
    cap_text_lines.append(f"\nid: <code>{html.escape(approval_id)}</code>")

    # News posts usam "Postar agora" — distingue de Aprovar (esteira do calendar).
    # On_brief_approve detecta news e força scheduled_at=now (real-time).
    keyboard = api.inline_keyboard([
        [("🚀 Postar agora", f"approve:{approval_id}"), ("❌ Rejeitar", f"reject:{approval_id}")],
        [("✏️ Ajustar", f"adjust:{approval_id}")],
    ])
    api.send_message("\n".join(cap_text_lines), reply_markup=keyboard)


def dispatch_one(item: dict, slot_label: str) -> bool:
    """Gera 1 feed post a partir de news item. Retorna True se preview enviado."""
    try:
        brief, plan_entry, review = _build_brief(item, slot_label)
    except Exception as e:  # noqa: BLE001
        notify(f"❌ feed news build_brief falhou: <code>{html.escape(str(e)[:200])}</code>")
        return False

    try:
        autogen_runner._save_brief_json(brief)  # type: ignore[attr-defined]
        autogen_runner._append_caption_md(brief)  # type: ignore[attr-defined]
        ok, err = autogen_runner._render_brief(brief["id"])  # type: ignore[attr-defined]
        if not ok:
            notify(
                f"⚠️ render falhou em feed news <code>{html.escape(brief['id'])}</code>\n"
                f"<pre>{html.escape(err)}</pre>"
            )
            return False
    except Exception as e:  # noqa: BLE001
        notify(f"❌ feed news render falhou: <code>{html.escape(str(e)[:200])}</code>")
        return False

    aid = autogen_runner._create_approval(brief, plan_entry, review)  # type: ignore[attr-defined]
    # Augmenta com news_context pra regen ter contexto
    approval = bot_state.read_approval(aid) or {}
    approval["news_context"] = {
        "title": item.get("title", ""),
        "source": item.get("feed_name", ""),
        "url": item.get("link", ""),
        "summary": item.get("summary", ""),
        "post_event": item.get("post_event", False),
        "modality": plan_entry["modality"],
    }
    approval["is_news_scheduled"] = True
    approval["news_score"] = item.get("score")
    bot_state.write_approval(approval)

    # Marca como usado já no dispatch (evita stories pegar o mesmo)
    _mark_used_in_feed(item.get("hash", ""))

    # Commit do claim do R2 photo pool — se a CAMADA 0 do visual.py serviu
    # uma foto do pool, agora marcamos como used (deleta R2, cooldown 45d).
    # Idempotente: se não houve claim, retorna False silencioso.
    try:
        from news import r2_photo_pool
        r2_photo_pool.commit_claim(aid)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ r2_photo_pool.commit_claim erro: {e!r}")

    _send_preview(brief, plan_entry, review, aid, item, slot_label)

    # Backup do approval JSON + PNG em R2 — sobrevive a redeploy do Railway.
    # Sem isso, qualquer push entre dispatch e approval mata o post (filesystem
    # ephemeral derruba output/feed/<id>.png e output/bot_state/pending/<aid>.json).
    feed_png = ROOT / "output" / "feed" / f"{brief['id']}.png"
    r2_persist.backup(aid, feed_png)
    return True


def maybe_dispatch(now: datetime) -> int:
    """Slot 2x/dia: envia SHORTLIST top-3 text-only pro Telegram.

    Editor clica 🎨 Produzir no item escolhido → reusa action `produce_news`
    do handlers.py que dispara `dispatch_one` (render + preview document).
    Evita queimar writer+reviewer+render+Gemini em item que vai ser rejeitado.

    Retorna nº de itens no shortlist.
    """
    state_file = _slot_state_file(now)
    if state_file is None:
        return 0
    today = now.date().isoformat()
    if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == today:
        return 0

    items = _pick_top_unused_n(3)
    slot_label = "manha" if state_file == STATE_MORNING else "tarde"

    if not items:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(today, encoding="utf-8")
        notify(
            f"📰 feed news slot {slot_label} · pool sem item score≥{MIN_SCORE} pendente",
            silent=True,
        )
        return 0

    header = (
        f"📰 <b>FEED NEWS · SHORTLIST {now.strftime('%d/%m %Hh')}</b>\n"
        f"<i>slot {slot_label} · top {len(items)} score≥{MIN_SCORE} · clica 🎨 pra produzir</i>"
    )
    api.send_message(header, silent=True)
    sent = 0
    for i, item in enumerate(items, start=1):
        title = item.get("title", "?")[:160]
        src = item.get("feed_name", "?")
        score = float(item.get("score", 0))
        modality = (item.get("primary_modality") or "?").lower()
        angle = (item.get("angle_suggestion") or "")[:200]
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
        api.send_message(msg, reply_markup=kb, silent=True)
        sent += 1

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(today, encoding="utf-8")
    return sent
