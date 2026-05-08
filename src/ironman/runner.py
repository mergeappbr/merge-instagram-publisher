"""
Orquestrador do tracker:
  - dispatch_countdown(race, days) → brief único, preview com botões
  - dispatch_results(race, results) → 3 briefs (carrossel), preview com botões só no último slide
  - on_*_approve/reject → adiciona/remove do calendar.csv

Approvals usam dois kinds distintos:
  - race_countdown — post único, igual fluxo brief
  - race_results   — carrossel de 3 slides, send_carousel_preview()

Adjust (free-text) só funciona pra race_results: o texto é parseado por
results.parse_manual() e o carrossel é regenerado com os novos rankings.
Countdown não suporta ajuste de copy via bot (gerar/aprovar/rejeitar; pra
mudar copy edite o brief direto e re-renderize manualmente).
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from alerts import notify
from autogen import calendar_io
from bot import api, state

from . import briefs as briefs_mod
from . import config as race_cfg
from . import results as results_mod

# Janela de detecção de cluster — provas mesma kind dentro deste range no Telegram preview
CLUSTER_WINDOW_DAYS = 30

ROOT = Path(__file__).resolve().parent.parent.parent
BRIEFS_DIR = ROOT / "content" / "briefs"
CAPTIONS_PATH = ROOT / "content" / "captions.md"
OUT_FEED = ROOT / "output" / "feed"
OUT_STORY = ROOT / "output" / "stories"


# ---------- helpers ----------

def _save_brief_json(brief: dict) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = BRIEFS_DIR / f"{brief['id']}.json"
    payload = {k: v for k, v in brief.items() if k != "caption_md"}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _append_caption_md(brief: dict) -> None:
    """Anexa seção `## <id> · <título>` ao captions.md (se houver caption)."""
    body = (brief.get("caption_md") or "").strip()
    if not body:
        return
    CAPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    title_label = brief.get("title") or brief["id"]
    block = (
        f"\n\n---\n\n"
        f"## {brief['id']} · {title_label}\n"
        f"**Hook do post:** {body.splitlines()[0]}\n\n"
        f"{body}\n"
    )
    with CAPTIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(block)


def _remove_caption_section(brief_id: str) -> None:
    if not CAPTIONS_PATH.exists():
        return
    text = CAPTIONS_PATH.read_text(encoding="utf-8")
    marker = f"## {brief_id} ·"
    if marker not in text:
        return
    parts = text.split("\n---\n")
    kept = [seg for seg in parts if marker not in seg]
    CAPTIONS_PATH.write_text("\n---\n".join(kept), encoding="utf-8")


def _render_brief(brief_id: str) -> tuple[bool, str]:
    cmd = [sys.executable, str(ROOT / "src" / "render.py"), brief_id]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or f"exit={proc.returncode}").strip()[-300:]
    return False, tail


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").replace("<br>", "\n").replace("<br/>", "\n")


# ---------- countdown ----------

def _create_countdown_approval(brief: dict, race: dict, days: int) -> str:
    aid = state.new_approval_id()
    state.write_approval({
        "id": aid,
        "kind": "race_countdown",
        "title": brief.get("title") or brief["id"],
        "brief": brief,
        "race": race,
        "days": days,
        "created_at": int(datetime.now().timestamp()),
    })
    return aid


def _kind_label(kind: str) -> str:
    return {
        "ironman": "Ironman",
        "mtb": "MTB",
        "trail": "Trail",
        "marathon": "Maratona",
        "hyrox": "Hyrox",
    }.get(kind, (kind or "Race").title())


def _detect_cluster(race: dict) -> list[dict]:
    """Retorna outras provas mesma `kind` em até CLUSTER_WINDOW_DAYS dias da
    race atual. Usado pra avisar no preview do Telegram que existe sobreposição
    de eventos da mesma modalidade — ajuda a não saturar o feed.
    """
    try:
        all_races = race_cfg.load_races()
    except Exception:  # noqa: BLE001
        return []
    target_kind = race.get("kind")
    target_id = race.get("id")
    target_d = race_cfg.parse_race_date(race["date"])
    nearby: list[dict] = []
    for other in all_races:
        if other.get("id") == target_id or other.get("kind") != target_kind:
            continue
        try:
            other_d = race_cfg.parse_race_date(other["date"])
        except Exception:  # noqa: BLE001
            continue
        gap = abs((other_d - target_d).days)
        if gap <= CLUSTER_WINDOW_DAYS:
            nearby.append({"name": other.get("name", "?"), "date": other["date"], "gap": gap})
    return nearby


def _send_countdown_preview(brief: dict, race: dict, days: int, approval_id: str) -> dict:
    feed_png = OUT_FEED / f"{brief['id']}.png"
    if not feed_png.exists():
        notify(f"⚠️ render não produziu feed pra <code>{html.escape(brief['id'])}</code>")
        return {}

    head_plain = _strip_html(brief.get("vars", {}).get("HEADLINE", ""))
    lead_plain = _strip_html(brief.get("vars", {}).get("LEAD", ""))
    caption_body = brief.get("caption_md", "")

    kind_label = _kind_label(race.get("kind", ""))
    cap_lines = [
        f"🏁 <b>[{kind_label} · T-{days}] {html.escape(race.get('name','?'))}</b>",
        f"<i>{html.escape(race.get('location',''))} · {html.escape(race['date'])}</i>",
    ]

    cluster = _detect_cluster(race)
    if cluster:
        cap_lines.append("")
        cap_lines.append(
            f"⚠️ <b>cluster {kind_label.lower()}</b> "
            f"(mesma modalidade ≤{CLUSTER_WINDOW_DAYS}d):"
        )
        for c in cluster[:3]:
            cap_lines.append(
                f"· {html.escape(c['name'])} · {html.escape(c['date'])} "
                f"(gap {c['gap']}d)"
            )

    cap_lines += [
        "",
        f"<b>ARTE</b>",
        f"HEAD: {html.escape(head_plain)[:200]}",
        f"LEAD: {html.escape(lead_plain)[:300]}",
        "",
        f"<b>LEGENDA</b>",
        f"<pre>{html.escape(caption_body[:500])}</pre>",
        "",
        f"id: <code>{html.escape(approval_id)}</code>",
    ]
    full_caption = "\n".join(cap_lines)
    if len(full_caption) > 1024:
        full_caption = full_caption[:1020] + "..."

    keyboard = api.inline_keyboard([
        [("✅ Aprovar", f"approve:{approval_id}"), ("❌ Rejeitar", f"reject:{approval_id}")],
    ])
    return api.send_photo_file(str(feed_png), caption=full_caption, reply_markup=keyboard)


def dispatch_countdown(race: dict, days: int) -> bool:
    """Gera brief T-N, renderiza, manda preview no Telegram. Retorna True se ok."""
    brief = briefs_mod.build_countdown_brief(race, days)
    _save_brief_json(brief)
    _append_caption_md(brief)
    ok, err = _render_brief(brief["id"])
    if not ok:
        notify(
            f"⚠️ render falhou pra <code>{html.escape(brief['id'])}</code>\n"
            f"<pre>{html.escape(err)}</pre>"
        )
        return False
    aid = _create_countdown_approval(brief, race, days)
    _send_countdown_preview(brief, race, days, aid)
    return True


def on_race_countdown_approve(approval: dict) -> None:
    brief = approval["brief"]
    race = approval.get("race", {})
    days = approval.get("days", 0)
    note = f"T-{days} · {race.get('name','?')}"[:60]
    # Countdown não entra na esteira: vai no próximo HH:00 livre HOJE
    # (9h-21h). Esse é o timing certo — Tdays é sensível a janela do dia.
    when = calendar_io.next_free_round_hour()
    row = calendar_io.insert_at_first_free(
        post_id=brief["id"],
        fmt="static",
        theme="ironman" if race.get("kind") == "ironman" else "endurance",
        note=note,
        when=when,
    )
    notify(
        f"✅ <b>countdown aprovado</b> · <code>{html.escape(brief['id'])}</code>\n"
        f"slot {row['slot']} · {row['scheduled_at']}",
        silent=True,
    )


def on_race_countdown_reject(approval: dict) -> None:
    brief = approval.get("brief") or {}
    bid = brief.get("id")
    if not bid:
        return
    for p in (BRIEFS_DIR / f"{bid}.json", OUT_FEED / f"{bid}.png", OUT_STORY / f"{bid}.png"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _remove_caption_section(bid)


# ---------- results (carrossel) ----------

def _create_results_approval(briefs_list: list[dict], race: dict, results: dict) -> str:
    aid = state.new_approval_id()
    state.write_approval({
        "id": aid,
        "kind": "race_results",
        "title": briefs_list[0].get("title") or briefs_list[0]["id"],
        "briefs": briefs_list,        # 3 briefs (cover, male, female)
        "race": race,
        "results": results,
        "created_at": int(datetime.now().timestamp()),
    })
    return aid


def _send_results_preview(
    briefs_list: list[dict], race: dict, results: dict, approval_id: str
) -> dict:
    paths: list[str] = []
    for b in briefs_list:
        png = OUT_FEED / f"{b['id']}.png"
        if not png.exists():
            notify(f"⚠️ render não produziu <code>{html.escape(b['id'])}</code>")
            return {}
        paths.append(str(png))

    cover = briefs_list[0]
    caption_body = cover.get("caption_md", "")
    warns = results.get("warnings") or []
    verified = results.get("verified")
    source = results.get("source", "?")
    badge = "✅ verificado" if verified else "⚠️ revisar"

    cap_lines = [
        f"🏆 <b>[Ironman · T+1] {html.escape(race.get('name','?'))}</b>",
        f"<i>resultados · fonte: {html.escape(source)} · {badge}</i>",
        "",
        f"<b>LEGENDA (capa)</b>",
        f"<pre>{html.escape(caption_body[:500])}</pre>",
    ]
    if warns:
        cap_lines.append("")
        cap_lines.append("<b>warnings</b>")
        for w in warns[:5]:
            cap_lines.append(f"⚠️ {html.escape(str(w))}")
    cap_lines.append("")
    cap_lines.append(
        '<i>p/ corrigir nomes/tempos: clique "✏️ Ajustar" e cole na forma:</i>'
    )
    cap_lines.append("<pre>masculino:\n1. Nome 08:23:11\n2. ...\nfeminino:\n1. ...</pre>")
    cap_lines.append(f"id: <code>{html.escape(approval_id)}</code>")

    full_caption = "\n".join(cap_lines)
    if len(full_caption) > 1024:
        full_caption = full_caption[:1020] + "..."

    keyboard = api.inline_keyboard([
        [("✅ Aprovar", f"approve:{approval_id}"), ("❌ Rejeitar", f"reject:{approval_id}")],
        [("✏️ Ajustar", f"adjust:{approval_id}")],
    ])
    return api.send_carousel_preview(paths, caption=full_caption, reply_markup=keyboard)


def dispatch_results(race: dict, results: dict) -> bool:
    """Gera 3 briefs do carrossel, renderiza, manda preview no Telegram."""
    briefs_list = briefs_mod.build_results_briefs(race, results)
    for b in briefs_list:
        _save_brief_json(b)
    # Caption só vai pro captions.md no nome da capa.
    _append_caption_md(briefs_list[0])
    for b in briefs_list:
        ok, err = _render_brief(b["id"])
        if not ok:
            notify(
                f"⚠️ render falhou em <code>{html.escape(b['id'])}</code>\n"
                f"<pre>{html.escape(err)}</pre>"
            )
            return False
    aid = _create_results_approval(briefs_list, race, results)
    _send_results_preview(briefs_list, race, results, aid)
    return True


def on_race_results_approve(approval: dict) -> None:
    briefs_list = approval.get("briefs") or []
    race = approval.get("race", {})
    if not briefs_list:
        return
    cover_id = briefs_list[0]["id"]
    note = f"T+1 resultados · {race.get('name','?')}"[:60]
    row = calendar_io.insert_at_first_free(
        post_id=cover_id,
        fmt="carousel",
        theme="ironman",
        note=note,
    )
    notify(
        f"✅ <b>resultados aprovados</b> · <code>{html.escape(cover_id)}</code>\n"
        f"slot {row['slot']} · {row['scheduled_at']} · carrossel ({len(briefs_list)} slides)",
        silent=True,
    )


def on_race_results_reject(approval: dict) -> None:
    briefs_list = approval.get("briefs") or []
    for b in briefs_list:
        bid = b.get("id")
        if not bid:
            continue
        for p in (BRIEFS_DIR / f"{bid}.json", OUT_FEED / f"{bid}.png", OUT_STORY / f"{bid}.png"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    if briefs_list:
        _remove_caption_section(briefs_list[0]["id"])


def on_race_results_regen(approval: dict, instruction: str) -> None:
    """Pedro respondeu com texto livre. Tentamos parsear como entrada manual de
    rankings; se sair top10 M+F, regeramos o carrossel. Senão, devolvemos erro."""
    parsed = results_mod.parse_manual(instruction)
    if parsed is None or not parsed.get("male"):
        notify(
            "⚠️ não consegui parsear os rankings.\n"
            "Formato esperado:\n"
            "<pre>masculino:\n1. Nome 08:23:11\n2. ...\nfeminino:\n1. ...</pre>"
        )
        return
    race = approval.get("race") or {}
    # Limpa briefs/PNGs antigos antes de regenerar
    old_briefs = approval.get("briefs") or []
    for b in old_briefs:
        bid = b.get("id")
        if not bid:
            continue
        for p in (BRIEFS_DIR / f"{bid}.json", OUT_FEED / f"{bid}.png"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    if old_briefs:
        _remove_caption_section(old_briefs[0]["id"])
    # Arquiva approval anterior, gera novo
    state.archive_approval(approval["id"], decision="regenerated")
    dispatch_results(race, parsed)
