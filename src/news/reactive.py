"""
Reactive post pipeline — quando uma notícia score >= 8 (ou pós-evento >=7),
gera brief reativo, renderiza, manda preview no Telegram com SLA de <10min.

Cooldown: 1 reativo PUBLICADO por dia (não por preview).

Aprovação tardia: se Pedro responder após 30min, ainda publica (decisão dele).
"""
from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from alerts import notify

from autogen import calendar_io, reviewer, runner as autogen_runner, writer
from news import visual

ROOT = Path(__file__).resolve().parent.parent.parent
REACTIVE_STATE = ROOT / "output" / ".last_reactive_published.txt"
COOLDOWN_HOURS = 24


def _slug(s: str, max_len: int = 30) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s.lower()).strip("_")
    return s[:max_len] or "news"


def _cooldown_active() -> bool:
    if not REACTIVE_STATE.exists():
        return False
    try:
        ts = datetime.fromisoformat(REACTIVE_STATE.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    return (datetime.now() - ts) < timedelta(hours=COOLDOWN_HOURS)


def mark_reactive_published() -> None:
    REACTIVE_STATE.parent.mkdir(parents=True, exist_ok=True)
    REACTIVE_STATE.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def trigger_reactive_post(item: dict) -> None:
    """Gera brief reativo a partir de item de notícia + manda preview."""
    if _cooldown_active():
        notify(
            f"📰 notícia score {item.get('score','?')} detectada mas reativo em cooldown:\n"
            f"<i>{html.escape(item.get('title','?')[:100])}</i> — {html.escape(item.get('feed_name',''))}",
            silent=True,
        )
        return

    bid = f"reactive_{datetime.now().strftime('%Y%m%d_%H%M')}_{_slug(item.get('title','news'), 20)}"
    plan_entry = {
        "scheduled_at": _next_slot_iso(),
        "slot_hour": datetime.now().hour,
        "theme": "news",
        "modality": item.get("primary_modality") or (item.get("modalities") or ["wellness"])[0],
        "format": "static",
        "template": "stat" if item.get("post_event") else "feature",
        "pillar": "news-reativo",
        "hook_idea": item.get("angle_suggestion") or item.get("title", ""),
        "lead_idea": item.get("summary", "")[:300],
        "caption_angle": item.get("angle_suggestion") or item.get("title", ""),
        "is_wildcard": True,
    }

    news_context = {
        "title": item.get("title", ""),
        "source": item.get("feed_name", ""),
        "url": item.get("link", ""),
        "summary": item.get("summary", ""),
        "post_event": item.get("post_event", False),
        "modality": plan_entry["modality"],
    }

    try:
        brief = writer.write_brief(plan_entry, news_context=news_context)
        brief["id"] = bid
        # Lock template + vars do magazine (mesmo padrão do feed_post).
        brief["template"] = "news_magazine"
        vars_ = brief.setdefault("vars", {})
        vars_["SOURCE"] = (item.get("feed_name") or news_context["source"] or "—").strip()
        if "story_vars" in brief:
            sv = brief["story_vars"]
            sv.setdefault("SOURCE", vars_["SOURCE"])
        # bg_override do item (lançamento com foto oficial) tem precedência;
        # senão resolve via Wikipedia (entity) ou FLUX (scene).
        bg_source = "writer-default"
        bg_final = vars_.get("BG_IMAGE", "—")
        if item.get("bg_override"):
            vars_["BG_IMAGE"] = item["bg_override"]
            if "story_vars" in brief:
                brief["story_vars"]["BG_IMAGE"] = item["bg_override"]
            bg_source = "override"
            bg_final = item["bg_override"]
        else:
            bg_url = visual.resolve_bg_for_news(
                aid=bid,
                title=news_context["title"],
                summary=news_context["summary"],
                modality=plan_entry["modality"],
            )
            if bg_url:
                vars_["BG_IMAGE"] = bg_url
                if "story_vars" in brief:
                    brief["story_vars"]["BG_IMAGE"] = bg_url
                bg_source = "visual.resolve_bg"
                bg_final = bg_url
            else:
                bg_source = "visual.resolve_bg→None (fallback writer)"
        brief["_bg_debug"] = {"source": bg_source, "final": bg_final}
        review = reviewer.review(brief)
    except Exception as e:  # noqa: BLE001
        notify(
            f"❌ <b>news reativa</b> · falha gerando brief: "
            f"<code>{html.escape(str(e)[:200])}</code>"
        )
        return

    try:
        autogen_runner._save_brief_json(brief)  # type: ignore[attr-defined]
        autogen_runner._append_caption_md(brief)  # type: ignore[attr-defined]
        ok, err = autogen_runner._render_brief(brief["id"])  # type: ignore[attr-defined]
        if not ok:
            notify(
                f"⚠️ render falhou em reativo <code>{html.escape(brief['id'])}</code>\n"
                f"<pre>{html.escape(err)}</pre>"
            )
            return
    except Exception as e:  # noqa: BLE001
        notify(
            f"❌ <b>news reativa</b> · render/save falhou: "
            f"<code>{html.escape(str(e)[:200])}</code>"
        )
        return

    # Approval — kind='brief' (reusa fluxo). Anota news_context pra regen ter contexto.
    aid = autogen_runner._create_approval(brief, plan_entry, review)  # type: ignore[attr-defined]
    # Augmenta o approval com news_context pra regen
    from bot import state as bot_state
    approval = bot_state.read_approval(aid) or {}
    approval["news_context"] = news_context
    approval["is_reactive"] = True
    approval["news_score"] = item.get("score")
    bot_state.write_approval(approval)

    # Preview customizado (não usa send_preview padrão pra incluir badge REATIVO)
    _send_reactive_preview(brief, plan_entry, review, aid, item)


def _next_slot_iso() -> str:
    """Próximo slot livre como string 'YYYY-MM-DD HH:MM'."""
    slot = calendar_io.next_free_slots(1)[0]
    return slot.strftime("%Y-%m-%d %H:%M")


def _send_reactive_preview(
    brief: dict,
    plan_entry: dict,
    review: dict,
    approval_id: str,
    item: dict,
) -> None:
    from bot import api

    feed_png = ROOT / "output" / "feed" / f"{brief['id']}.png"
    if not feed_png.exists():
        notify(f"⚠️ render reativo não gerou {feed_png.name}")
        return

    vars_ = brief.get("vars", {})
    head_plain = re.sub(r"<[^>]+>", "", vars_.get("HEADLINE", ""))
    lead_plain = re.sub(r"<[^>]+>", "", vars_.get("LEAD", "")).replace("<br>", "\n").replace("<br/>", "\n")
    caption = brief.get("caption_md", "")
    sched = plan_entry.get("scheduled_at", "?")
    score = item.get("score", "?")

    warns = review.get("warnings", [])
    blockers = review.get("blockers", [])
    review_block = ""
    if warns or blockers:
        bullets = [f"❗ {b}" for b in blockers] + [f"⚠️ {w}" for w in warns]
        review_block = "\n\n<b>review</b>\n" + "\n".join(bullets[:6])

    cap_lines = [
        f"📰 <b>REATIVO · score {score}</b>",
        f"<i>{html.escape(item.get('feed_name','?'))}</i>",
        f"→ {html.escape(item.get('title','')[:120])}",
        "",
        f"sched: {html.escape(sched)} · template {html.escape(brief.get('template','?'))}",
        "",
        f"<b>ARTE</b>",
        f"HEAD: {html.escape(head_plain)[:200]}",
        f"LEAD: {html.escape(lead_plain)[:300]}",
        "",
        f"<b>LEGENDA</b>",
        f"<pre>{html.escape(caption[:450])}</pre>",
    ]
    if review_block:
        cap_lines.append(review_block)
    cap_lines.append(f"\nid: <code>{html.escape(approval_id)}</code>")

    full_caption = "\n".join(cap_lines)
    if len(full_caption) > 1024:
        full_caption = full_caption[:1020] + "..."

    keyboard = api.inline_keyboard(
        [
            [("✅ Aprovar", f"approve:{approval_id}"), ("❌ Rejeitar", f"reject:{approval_id}")],
            [("✏️ Ajustar", f"adjust:{approval_id}")],
        ]
    )
    api.send_photo_file(
        str(feed_png),
        caption=full_caption,
        reply_markup=keyboard,
    )
