"""
Runner do autogen — orquestra:
  1. checa runway via calendar_io
  2. se < THRESHOLD, gera lote semanal: planner → 14 writers → 14 reviewers
  3. pra cada brief: render PNG (feed + story), escreve JSON em content/briefs/,
     escreve caption em content/captions.md, manda preview no Telegram
  4. quando humano aprova: adiciona linha ao calendar.csv, persistência ok
  5. quando humano rejeita: descarta brief + assets

Também serve como handler de regen quando o humano pede ajuste.
"""
from __future__ import annotations

import csv
import html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from alerts import notify
from bot import api, state

from . import calendar_io, planner, reviewer, store, writer

ROOT = Path(__file__).resolve().parent.parent.parent
BRIEFS_DIR = ROOT / "content" / "briefs"
CAPTIONS_PATH = ROOT / "content" / "captions.md"
OUT_FEED = ROOT / "output" / "feed"
OUT_STORY = ROOT / "output" / "stories"
RUNWAY_TRIGGER_DAYS = 14
DEFAULT_BATCH_SLOTS = 14  # 7 dias * 2 slots
GEN_STATE = ROOT / "output" / ".last_autogen_run.txt"
COOLDOWN_HOURS = 6  # não reroda autogen mais que isso (evita race)


# --------------------- preview helpers ---------------------

def _render_brief(brief_id: str) -> tuple[bool, str]:
    """Chama src/render.py pro brief. Retorna (ok, error_tail) onde error_tail é
    o final do stderr/stdout (últimos ~300 chars) quando ok=False; vazio quando ok."""
    cmd = [sys.executable, str(ROOT / "src" / "render.py"), brief_id]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()
    # pega últimas linhas, limita a 300 chars
    tail = tail[-300:]
    return False, tail


def _save_brief_json(brief: dict) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{brief['id']}.json"
    payload = {k: v for k, v in brief.items() if k not in ("caption_md", "carousel_slides")}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _append_caption_md(brief: dict) -> None:
    """Adiciona seção '## <id> · TÍTULO' ao captions.md."""
    if not brief.get("caption_md"):
        return
    CAPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    title_label = brief.get("title") or brief["id"]
    body = brief["caption_md"].strip()
    block = (
        f"\n\n---\n\n"
        f"## {brief['id']} · {title_label}\n"
        f"**Hook do post:** {body.splitlines()[0]}\n\n"
        f"{body}\n"
    )
    with CAPTIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(block)


def _send_preview(brief: dict, plan_entry: dict, review: dict, approval_id: str) -> dict:
    """Envia foto + caption + botões no Telegram. Retorna response da API."""
    feed_png = OUT_FEED / f"{brief['id']}.png"
    if not feed_png.exists():
        notify(
            f"⚠️ render não produziu feed pra "
            f"<code>{html.escape(brief['id'])}</code>"
        )
        return {}

    vars_ = brief.get("vars", {})
    headline = vars_.get("HEADLINE", "—")
    lead = vars_.get("LEAD", "—")
    pill = vars_.get("PILL", "")
    caption_body = brief.get("caption_md", "")
    sched = plan_entry.get("scheduled_at", "?")
    template = brief.get("template", "?")
    fmt = "carousel" if template == "carousel_cover" else "static"

    warns = review.get("warnings", [])
    blockers = review.get("blockers", [])
    review_block = ""
    if warns or blockers:
        bullets = []
        for b in blockers:
            bullets.append(f"❗ {b}")
        for w in warns:
            bullets.append(f"⚠️ {w}")
        review_block = "\n\n<b>review</b>\n" + "\n".join(bullets[:8])

    # Strip HTML tags do headline pra caption do telegram (Telegram tem HTML mode próprio)
    import re as _re
    head_plain = _re.sub(r"<[^>]+>", "", headline)
    lead_plain = _re.sub(r"<[^>]+>", "", lead).replace("<br>", "\n").replace("<br/>", "\n")

    cap_lines = [
        f"📋 <b>BRIEF · {html.escape(plan_entry.get('theme','?'))}</b>",
        f"<i>{html.escape(sched)} · {html.escape(template)} · {html.escape(fmt)}</i>",
        "",
        f"<b>ARTE</b>",
        f"PILL: {html.escape(pill)}",
        f"HEAD: {html.escape(head_plain)[:200]}",
        f"LEAD: {html.escape(lead_plain)[:300]}",
        "",
        f"<b>LEGENDA</b>",
        f"<pre>{html.escape(caption_body[:500])}</pre>",
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
    return api.send_photo_file(
        str(feed_png),
        caption=full_caption,
        reply_markup=keyboard,
    )


# --------------------- approval flow ---------------------

def _create_approval(brief: dict, plan_entry: dict, review: dict) -> str:
    aid = state.new_approval_id()
    state.write_approval(
        {
            "id": aid,
            "kind": "brief",
            "title": brief.get("title", brief.get("id", "?")),
            "brief": brief,
            "plan_entry": plan_entry,
            "review": review,
            "created_at": int(datetime.now().timestamp()),
        }
    )
    return aid


def on_brief_approve(approval: dict) -> None:
    """Humano aprovou. Adiciona ao calendar.csv no scheduled_at planejado.

    News posts (theme=news OU is_news_scheduled=True) sobrescrevem o slot
    planejado e ficam scheduled_at=agora — assim o próximo tick do scheduler
    publica em <1min. Sem isso, news cai no próximo slot livre do calendar
    (pode ser semanas no futuro) e perde o timing de real-time.
    """
    brief = approval["brief"]
    plan_entry = approval["plan_entry"]
    is_news = (
        approval.get("is_news_scheduled")
        or plan_entry.get("theme") == "news"
    )
    if is_news:
        # Real-time: publica no próximo tick (1 min de janela é ok)
        when = datetime.now()
    else:
        sched = plan_entry.get("scheduled_at", "")
        try:
            when = datetime.strptime(sched, "%Y-%m-%d %H:%M")
        except ValueError:
            # fallback: próximo slot livre
            when = calendar_io.next_free_slots(1)[0]
    fmt = plan_entry.get("format", "static")
    theme = plan_entry.get("theme", "")
    note = brief.get("title", brief.get("id", ""))[:60]
    row = calendar_io.insert_at_first_free(
        post_id=brief["id"],
        fmt=fmt,
        theme=theme,
        note=note,
        when=when,
    )
    notify(
        f"✅ <b>brief aprovado</b> · <code>{html.escape(brief['id'])}</code>\n"
        f"slot {row['slot']} · {row['scheduled_at']} · {row['format']}",
        silent=True,
    )


def on_brief_reject(approval: dict) -> None:
    """Humano rejeitou. Apaga arquivos do brief."""
    brief = approval.get("brief") or {}
    bid = brief.get("id")
    if not bid:
        return
    paths = [
        BRIEFS_DIR / f"{bid}.json",
        OUT_FEED / f"{bid}.png",
        OUT_STORY / f"{bid}.png",
    ]
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    # Remove seção do captions.md (best-effort).
    if CAPTIONS_PATH.exists():
        text = CAPTIONS_PATH.read_text(encoding="utf-8")
        marker = f"## {bid} ·"
        if marker in text:
            parts = text.split("\n---\n")
            kept = [seg for seg in parts if marker not in seg]
            CAPTIONS_PATH.write_text("\n---\n".join(kept), encoding="utf-8")


def on_brief_regen(approval: dict, instruction: str) -> None:
    """Regenera brief com instrução. Reescreve arquivos e re-envia preview."""
    plan_entry = approval.get("plan_entry") or {}
    previous = approval.get("brief") or {}
    news_context = approval.get("news_context")
    new_brief = writer.regenerate_brief(
        plan_entry, previous, instruction, news_context=news_context
    )
    # Mantém mesmo id pra reaproveitar paths/render.
    new_brief["id"] = previous.get("id", new_brief.get("id"))
    review = reviewer.review(new_brief)
    _save_brief_json(new_brief)
    _replace_caption_md(new_brief)
    ok, err = _render_brief(new_brief["id"])
    if not ok:
        notify(
            f"⚠️ render falhou após ajuste de <code>{html.escape(new_brief['id'])}</code>\n"
            f"<pre>{html.escape(err)}</pre>"
        )
        return
    # Nova approval (descarta a antiga via archive na decisão).
    new_aid = _create_approval(new_brief, plan_entry, review)
    # Arquiva a anterior como "regenerated"
    state.archive_approval(approval["id"], decision="regenerated", extra={"replaced_by": new_aid})
    _send_preview(new_brief, plan_entry, review, new_aid)


def _replace_caption_md(brief: dict) -> None:
    """Substitui seção do captions.md pelo conteúdo novo (idempotente)."""
    if not CAPTIONS_PATH.exists():
        _append_caption_md(brief)
        return
    text = CAPTIONS_PATH.read_text(encoding="utf-8")
    marker = f"## {brief['id']} ·"
    if marker not in text:
        _append_caption_md(brief)
        return
    # Remove bloco antigo entre 2 separadores `---`
    segments = text.split("\n---\n")
    kept = [seg for seg in segments if marker not in seg]
    CAPTIONS_PATH.write_text("\n---\n".join(kept), encoding="utf-8")
    _append_caption_md(brief)


# --------------------- batch generation ---------------------

def _cooldown_active() -> bool:
    if not GEN_STATE.exists():
        return False
    try:
        ts = datetime.fromisoformat(GEN_STATE.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    return (datetime.now() - ts).total_seconds() < COOLDOWN_HOURS * 3600


def _mark_run() -> None:
    GEN_STATE.parent.mkdir(parents=True, exist_ok=True)
    GEN_STATE.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def _insights_hint() -> str:
    """Pega top/bot da última semana pra alimentar o planner."""
    try:
        from insights.collector import INSIGHTS_FIELDS  # noqa: F401
    except Exception:
        pass
    INSIGHTS = ROOT / "output" / "insights.csv"
    if not INSIGHTS.exists():
        return ""
    by_id: dict[str, dict] = {}
    with INSIGHTS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mid = row.get("media_id", "")
            if not mid:
                continue
            prev = by_id.get(mid)
            if prev is None or row.get("snapshot_date", "") >= prev.get("snapshot_date", ""):
                by_id[mid] = row
    snaps = list(by_id.values())
    if not snaps:
        return ""

    def eng(r: dict) -> float:
        def i(v): return int(v or 0) if str(v).lstrip("-").isdigit() else 0
        return i(r.get("likes")) + 3 * i(r.get("comments")) + 2 * i(r.get("saved")) + 4 * i(r.get("shares"))

    ranked = sorted(snaps, key=eng, reverse=True)
    top = ranked[:3]
    bot = ranked[-3:][::-1] if len(ranked) > 3 else []
    parts = ["TOP 3 (engajamento ponderado):"]
    for s in top:
        parts.append(f"  · {s.get('post_id','?')} ({s.get('format','?')}) eng={int(eng(s))}")
    if bot:
        parts.append("BOTTOM 3:")
        for s in bot:
            parts.append(f"  · {s.get('post_id','?')} ({s.get('format','?')}) eng={int(eng(s))}")
    return "\n".join(parts)


def maybe_generate_batch() -> int:
    """Roda lote semanal se runway < threshold e cooldown expirou. Retorna nº enviados."""
    if _cooldown_active():
        return 0
    runway = calendar_io.runway_days()
    if runway >= RUNWAY_TRIGGER_DAYS:
        return 0
    notify(
        f"🤖 <b>autogen</b> · runway <b>{runway}d</b> &lt; {RUNWAY_TRIGGER_DAYS}d. "
        f"gerando lote semanal…",
        silent=True,
    )
    week_start = datetime.now()
    try:
        plan = planner.plan_week(week_start, n_slots=DEFAULT_BATCH_SLOTS, insights_hint=_insights_hint())
    except Exception as e:  # noqa: BLE001
        notify(f"❌ <b>autogen</b> planner falhou: <code>{html.escape(str(e)[:300])}</code>")
        _mark_run()
        return 0

    sent = 0
    recent = store.list_recent_briefs(window_days=60)
    for entry in plan:
        if entry.get("is_wildcard"):
            # wildcard fica reservado pro news watcher; não geramos brief estático.
            continue
        try:
            brief = writer.write_brief(entry)
            review = reviewer.review(brief)
            # Sanity: se reviewer bloquear, regenera 1x com instrução automática
            if not review["ok"] and review.get("blockers"):
                instruction = (
                    "remova claims médicos arriscados (cura, garante, 100% eficaz, "
                    "elimina); reformule com linguagem responsável citando fonte."
                )
                brief = writer.regenerate_brief(entry, brief, instruction)
                review = reviewer.review(brief)
            _save_brief_json(brief)
            _append_caption_md(brief)
            ok, err = _render_brief(brief["id"])
            if not ok:
                notify(
                    f"⚠️ render falhou em <code>{html.escape(brief['id'])}</code>; pulando\n"
                    f"<pre>{html.escape(err)}</pre>"
                )
                continue
            aid = _create_approval(brief, entry, review)
            _send_preview(brief, entry, review, aid)
            sent += 1
        except Exception as e:  # noqa: BLE001
            notify(
                f"⚠️ falha gerando slot {html.escape(entry.get('scheduled_at','?'))}: "
                f"<code>{html.escape(str(e)[:200])}</code>"
            )
            continue
        # diversidade — atualiza recentes
        recent.append(
            {
                "id": brief["id"],
                "template": brief.get("template", ""),
                "title": brief.get("title", ""),
                "vars": brief.get("vars", {}),
                "scheduled_at": entry.get("scheduled_at", ""),
                "theme": entry.get("theme", ""),
                "_keywords": store.keywords(
                    " ".join([brief.get("title", ""), brief.get("vars", {}).get("HEADLINE", "")])
                ),
            }
        )

    _mark_run()
    notify(f"🤖 autogen · {sent} preview(s) enviado(s) pra aprovação", silent=True)
    return sent
