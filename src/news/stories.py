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
    """Pega top-N items não usados em story OU feed.

    Coordenação com feed_post.py: feed_post marca `used_in_feed` no dispatch
    (não na aprovação) pra reservar o top item; stories pega os 2 seguintes.
    Se feed_post não rodou ainda no slot, stories pega top-N normal — o item
    top vai pra stories e na próxima rodada feed_post pega outro (o pool
    rotaciona naturalmente).
    """
    pool = _load_pool()
    pending = [
        p for p in pool
        if not p.get("used_in_story") and not p.get("used_in_feed")
    ]
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
    # send_document_file: sem letterbox/compressão do Telegram — preview pixel-perfect
    api.send_document_file(str(story_png), caption=cap, reply_markup=keyboard)


def _send_story_shortlist(items: list[dict], slot_label: str) -> int:
    """Manda shortlist de stories candidatos pro Telegram SEM renderizar arte.

    Cada item vira uma mensagem texto com botão 🎨 Produzir story que, ao ser
    clicado, dispara `produce_story_by_hash` → renderiza + envia preview real.

    Evita queimar render/Gemini em itens que o editor não vai aprovar.
    """
    if not items:
        return 0
    header = (
        f"📱 <b>STORIES · SHORTLIST {datetime.now().strftime('%d/%m %Hh')}</b>\n"
        f"<i>slot {slot_label} · clica 🎨 pra gerar arte do escolhido</i>"
    )
    api.send_message(header, silent=True)

    sent = 0
    for i, item in enumerate(items, start=1):
        title = item.get("title", "?")[:160]
        src = item.get("feed_name", "?")
        score = item.get("score", 0)
        modality = (item.get("primary_modality") or "?").lower()
        link = item.get("link", "")
        h = item.get("hash", "")
        msg = (
            f"<b>#{i} · score {float(score):.1f}</b> · <i>{html.escape(src)}</i> · "
            f"<code>{html.escape(modality)}</code>\n"
            f"<b>{html.escape(title)}</b>"
        )
        summary = (item.get("summary") or "").strip()
        if summary:
            msg += f"\n<i>{html.escape(summary[:240])}</i>"
        if link:
            msg += f"\n🔗 {html.escape(link)}"
        kb = api.inline_keyboard([
            [("🎨 Produzir story", f"produce_story:{h[:32]}"),
             ("⏭️ Pular", f"skip_story:{h[:32]}")],
        ])
        api.send_message(msg, reply_markup=kb, silent=True)
        sent += 1
    return sent


def produce_story_by_hash(hash_prefix: str, chat_id: int | None = None) -> bool:
    """Acha item no pool por prefixo de hash, renderiza story e envia preview.

    Chamado pelo callback `produce_story:<hash>` em bot/handlers.py.
    Retorna True se preview foi enviado.
    """
    pool = _load_pool()
    item = next(
        (p for p in pool if (p.get("hash") or "").startswith(hash_prefix)),
        None,
    )
    if item is None:
        api.send_message(
            f"⚠️ item <code>{html.escape(hash_prefix)}</code> não está no pool.",
            chat_id=str(chat_id) if chat_id else None,
        )
        return False
    if item.get("used_in_story") or item.get("used_in_feed"):
        api.send_message(
            f"⚠️ item <code>{html.escape(hash_prefix)}</code> já foi usado.",
            chat_id=str(chat_id) if chat_id else None,
        )
        return False
    api.send_message(
        f"⏳ gerando story: <i>{html.escape(item.get('title','?')[:80])}</i>",
        chat_id=str(chat_id) if chat_id else None,
        silent=True,
    )
    try:
        brief = _make_story_brief(item)
        _save_brief_json(brief)
        if not _render_story_only(brief["id"]):
            notify(f"⚠️ render falhou em story <code>{html.escape(brief['id'])}</code>")
            return False
        aid = _create_approval(brief, item)
        _preview_story(brief, aid, item)
        return True
    except Exception as e:  # noqa: BLE001
        notify(f"⚠️ produce_story falhou: <code>{html.escape(str(e)[:200])}</code>")
        return False


def skip_story_by_hash(hash_prefix: str) -> None:
    """Marca item como usado pra não reaparecer. Chamado por skip_story callback."""
    pool = _load_pool()
    for p in pool:
        if (p.get("hash") or "").startswith(hash_prefix):
            p["used_in_story"] = True
            p["used_at"] = datetime.now().isoformat(timespec="seconds")
    _save_pool(pool)


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
    """Slot 2x/dia: envia SHORTLIST text-only pro Telegram, sem renderizar arte.

    Editor clica 🎨 Produzir story no item escolhido — só aí roda render +
    Gemini + send_document. Evita queimar custo/quota em itens rejeitados.

    Retorna nº de itens no shortlist (não nº de stories publicados).
    """
    state_file = _slot_state_file(now)
    if state_file is None:
        return 0
    today = now.date().isoformat()
    if state_file.exists() and state_file.read_text(encoding="utf-8").strip() == today:
        return 0

    items = _next_unused_from_pool(STORIES_PER_RUN)
    slot_label = "tarde" if state_file == STORY_STATE_AFTERNOON else "manha"
    if not items:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(today, encoding="utf-8")
        notify(
            f"📱 stories slot {now.strftime('%H:%M')} · pool vazio (sem notícia score≥5 nas últimas 36h)",
            silent=True,
        )
        return 0

    sent = _send_story_shortlist(items, slot_label)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(today, encoding="utf-8")
    return sent
