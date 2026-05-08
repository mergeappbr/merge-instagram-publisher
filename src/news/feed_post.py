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
from autogen import reviewer, runner as autogen_runner, writer
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
    # Override de BG — quando o item traz `bg_override`, força no brief (e no
    # story_vars) sobrescrevendo o que o writer escolheu. Útil pra notícias
    # com foto oficial dedicada (ex: lançamento Fitbit Air).
    bg_override = item.get("bg_override")
    if bg_override:
        brief.setdefault("vars", {})["BG_IMAGE"] = bg_override
        if "story_vars" in brief:
            brief["story_vars"]["BG_IMAGE"] = bg_override
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

    # NEWS sempre usa o template magazine novo (foto full-bleed + headline
    # gigante + footer com source · merge.). Override pós-writer pra não
    # depender do LLM escolher template certo.
    brief["template"] = "news_magazine"
    vars_ = brief.setdefault("vars", {})
    vars_["PILL"] = "NEWS"
    vars_["SOURCE"] = (item.get("feed_name") or news_context["source"] or "—").strip()
    vars_["DATE_LABEL"] = datetime.now().strftime("%d/%m")
    if "story_vars" in brief:
        sv = brief["story_vars"]
        sv["PILL"] = "NEWS"
        sv.setdefault("SOURCE", vars_["SOURCE"])
        sv.setdefault("DATE_LABEL", vars_["DATE_LABEL"])

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
    cap_lines = [
        f"📰 <b>FEED NEWS · {slot_label.upper()} · score {score}</b>",
        f"<i>{html.escape(item.get('feed_name','?'))}</i>",
        f"→ {html.escape(item.get('title','')[:120])}",
        "",
        f"sched: {html.escape(plan_entry.get('scheduled_at','?'))} · "
        f"template {html.escape(brief.get('template','?'))}",
        "",
        f"<b>ARTE</b>",
        f"HEAD: {html.escape(head_plain)[:200]}",
        f"LEAD: {html.escape(lead_plain)[:300]}",
    ]
    photo_caption = "\n".join(cap_lines)
    if len(photo_caption) > 1024:
        photo_caption = photo_caption[:1020] + "..."

    api.send_photo_file(str(feed_png), caption=photo_caption)

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

    _send_preview(brief, plan_entry, review, aid, item, slot_label)

    # Backup do approval JSON + PNG em R2 — sobrevive a redeploy do Railway.
    # Sem isso, qualquer push entre dispatch e approval mata o post (filesystem
    # ephemeral derruba output/feed/<id>.png e output/bot_state/pending/<aid>.json).
    feed_png = ROOT / "output" / "feed" / f"{brief['id']}.png"
    r2_persist.backup(aid, feed_png)
    return True


def maybe_dispatch(now: datetime) -> int:
    """Dispara feed news se for janela e ainda não rodou hoje. Retorna 1 se ok."""
    state_file = _slot_state_file(now)
    if state_file is None:
        return 0
    today = now.date().isoformat()
    if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == today:
        return 0

    item = _pick_top_unused()
    slot_label = "manha" if state_file == STATE_MORNING else "tarde"

    if item is None:
        # Marca o slot mesmo sem item pra não tentar de novo no mesmo dia.
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(today, encoding="utf-8")
        notify(
            f"📰 feed news slot {slot_label} · pool sem item score≥{MIN_SCORE} pendente",
            silent=True,
        )
        return 0

    sent = 0
    if dispatch_one(item, slot_label):
        sent = 1

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(today, encoding="utf-8")
    return sent
