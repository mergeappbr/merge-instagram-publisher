"""
Stories de notícia 2x/dia (08:00 e 14:00 BRT).

Pipeline:
  1. Pega top 2 do news_pool.json (score >= 5, não usados)
  2. Gera arte stories (1080×1920) com modelo OFitFeed/TNS — direto, reto:
     - PILL gigante (modalidade)
     - HEADLINE clickbait (1ª linha do título)
     - LEAD: 1 frase de contexto
     - footer com handle
  3. Manda preview Telegram simplificado (Publicar / Pular / Próxima)
  4. Aprovado → publica direto no IG via publish_story()

State:
  output/.last_stories_run_morning.txt
  output/.last_stories_run_afternoon.txt
  output/news_pool.json (marca 'used_in_story': true)
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from alerts import notify
from bot import api, state as bot_state

ROOT = Path(__file__).resolve().parent.parent.parent
POOL_FILE = ROOT / "output" / "news_pool.json"
BRIEFS_DIR = ROOT / "content" / "briefs"
OUT_STORY = ROOT / "output" / "stories"
STORY_STATE_MORNING = ROOT / "output" / ".last_stories_morning.txt"
STORY_STATE_AFTERNOON = ROOT / "output" / ".last_stories_afternoon.txt"

MORNING_HOUR = 8
AFTERNOON_HOUR = 14
STORIES_PER_RUN = 2


def _slug(s: str, max_len: int = 24) -> str:
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


def _next_unused_from_pool(n: int) -> list[dict]:
    pool = _load_pool()
    pending = [p for p in pool if not p.get("used_in_story")]
    pending.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    return pending[:n]


def _mark_used(item_hash: str) -> None:
    pool = _load_pool()
    for p in pool:
        if p.get("hash") == item_hash:
            p["used_in_story"] = True
            p["used_at"] = datetime.now().isoformat(timespec="seconds")
    _save_pool(pool)


def _make_story_brief(item: dict) -> dict:
    """
    Gera brief minimalista de stories direto (sem LLM — formato fixo OFitFeed/TNS).
    Não passa pelo writer pra ser instantâneo (stories são tempo-crítico).
    """
    title = item.get("title", "").strip()
    headline = title[:80]  # arte vai mostrar
    summary = item.get("summary", "").strip()
    # 1ª frase do summary como lead
    lead = re.split(r"(?<=[.!?])\s+", summary)[0][:140] if summary else ""
    modality = (item.get("primary_modality") or "").upper().replace("_", " ") or "WELLNESS"
    bid = f"story_news_{datetime.now().strftime('%Y%m%d_%H%M')}_{_slug(title)}"

    return {
        "id": bid,
        "template": "feature",  # template feature funciona bem como story-only
        "pillar": "news-stories",
        "title": f"Stories news · {title[:60]}",
        "vars": {
            "PILL": modality,
            "HEADLINE": f'<span class="hl">{html.escape(headline)}</span>',
            "LEAD": html.escape(lead),
            "OVERLAY": "bottom",
        },
        "story_vars": {
            "PILL": modality,
            "HEADLINE": f'<span class="hl">{html.escape(headline)}</span>',
            "LEAD": html.escape(lead) + "<br><br>via <b>" + html.escape(item.get("feed_name", "")) + "</b>",
        },
        "_news_item": item,  # carrega contexto pro publish e archive
    }


def _save_brief_json(brief: dict) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in brief.items() if not k.startswith("_")}
    path = BRIEFS_DIR / f"{brief['id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _render_story_only(brief_id: str) -> bool:
    """Render com flag de só story (passa via env var, render.py respeita)."""
    cmd = [sys.executable, str(ROOT / "src" / "render.py"), brief_id]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode == 0


def _preview_story(brief: dict, approval_id: str, item: dict) -> None:
    story_png = OUT_STORY / f"{brief['id']}.png"
    if not story_png.exists():
        notify(f"⚠️ story render não gerou {story_png.name}")
        return
    title = item.get("title", "")
    score = item.get("score", "?")
    cap = (
        f"📱 <b>STORIES · {datetime.now().strftime('%H:%M')}</b>\n"
        f"<i>{html.escape(item.get('feed_name','?'))} · score {score}</i>\n\n"
        f"→ {html.escape(title[:200])}\n\n"
        f"id: <code>{html.escape(approval_id)}</code>"
    )
    keyboard = api.inline_keyboard(
        [
            [("✅ Publicar", f"approve:{approval_id}"), ("❌ Pular", f"reject:{approval_id}")],
        ]
    )
    api.send_photo_file(str(story_png), caption=cap, reply_markup=keyboard)


def _create_approval(brief: dict, item: dict) -> str:
    aid = bot_state.new_approval_id()
    bot_state.write_approval(
        {
            "id": aid,
            "kind": "story",
            "title": item.get("title", brief["id"])[:120],
            "brief": brief,
            "news_item": item,
            "created_at": int(datetime.now().timestamp()),
        }
    )
    return aid


# --------------------- callbacks (registrados pelo bot/poller) ---------------

def on_story_approve(approval: dict) -> None:
    """Publica story direto via publish.publish_story()."""
    brief = approval["brief"]
    item = approval.get("news_item", {})
    story_png = OUT_STORY / f"{brief['id']}.png"
    if not story_png.exists():
        notify(f"⚠️ story PNG sumiu: {story_png.name}")
        return

    try:
        from publish import publish_story
    except Exception as e:  # noqa: BLE001
        notify(f"❌ publish_story indisponível: {html.escape(str(e)[:200])}")
        return

    try:
        media_id = publish_story(str(story_png), post_id=brief["id"])
    except SystemExit as e:
        notify(f"❌ falha publicando story: <code>{html.escape(str(e)[:300])}</code>")
        return
    except Exception as e:  # noqa: BLE001
        notify(f"❌ exception publicando story: <code>{html.escape(str(e)[:300])}</code>")
        return

    _mark_used(item.get("hash", ""))
    notify(
        f"✅ <b>story publicado</b> · <code>{html.escape(brief['id'])}</code>\n"
        f"<i>{html.escape(item.get('title','')[:80])}</i>",
        silent=True,
    )
    if media_id:
        print(f"story media_id={media_id}")


def on_story_reject(approval: dict) -> None:
    item = approval.get("news_item", {})
    brief = approval.get("brief", {})
    _mark_used(item.get("hash", ""))  # marca como consumido pra não reaparecer
    bid = brief.get("id")
    if bid:
        for p in (BRIEFS_DIR / f"{bid}.json", OUT_STORY / f"{bid}.png"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


# --------------------- runner ---------------

def _slot_state_file(now: datetime) -> Path | None:
    """Decide qual slot de stories disparar agora.

    Lógica catch-up: depois das 8h, se o slot da manhã ainda não rodou hoje,
    dispara. Depois das 14h, se o da tarde não rodou, dispara. Garante que
    Railway restart/cold-start não faz a gente perder o dia inteiro.

    Tarde tem prioridade sobre manhã quando ambos pendentes (não acumular).
    """
    today = now.date().isoformat()

    def _done(p: Path) -> bool:
        return p.exists() and p.read_text(encoding="utf-8").strip() == today

    if now.hour >= AFTERNOON_HOUR and not _done(STORY_STATE_AFTERNOON):
        return STORY_STATE_AFTERNOON
    if now.hour >= MORNING_HOUR and not _done(STORY_STATE_MORNING):
        return STORY_STATE_MORNING
    return None


def maybe_dispatch(now: datetime) -> int:
    """Dispara stories se for janela. Retorna nº de previews enviados."""
    state_file = _slot_state_file(now)
    if state_file is None:
        return 0
    today = now.date().isoformat()
    if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == today:
        return 0

    items = _next_unused_from_pool(STORIES_PER_RUN)
    if not items:
        # marca o slot mesmo sem items pra não tentar de novo no mesmo dia
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(today, encoding="utf-8")
        notify(
            f"📱 stories slot {now.strftime('%H:%M')} · pool vazio (sem notícia score≥5 nas últimas {36}h)",
            silent=True,
        )
        return 0

    sent = 0
    for item in items:
        try:
            brief = _make_story_brief(item)
            _save_brief_json(brief)
            if not _render_story_only(brief["id"]):
                continue
            aid = _create_approval(brief, item)
            _preview_story(brief, aid, item)
            sent += 1
        except Exception as e:  # noqa: BLE001
            notify(f"⚠️ story falhou: <code>{html.escape(str(e)[:200])}</code>")

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(today, encoding="utf-8")
    return sent
