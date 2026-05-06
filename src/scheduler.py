"""
Scheduler do Merge — lê content/calendar.csv e publica no horário certo.

Roda em loop infinito (a cada 60s):
  1. lê calendar.csv (fonte da verdade do que está programado)
  2. lê output/published.csv (fonte da verdade do que já saiu)
  3. acha slots cujo scheduled_at <= agora E ainda não publicados
  4. invoca src/publish.py --post <id> pra cada slot devido
  5. publish.py já loga em published.csv após sucesso

Timezone: tudo em America/Sao_Paulo (calendar.csv guarda hora local SP).

Tolerância: se o worker dormiu, slots atrasados publicam quando ele acorda.
Slots com mais de MAX_STALENESS_MINUTES de atraso são pulados (com warning
único — o estado de pulado vai pra output/.skipped_slots.txt no volume).

Alertas via Telegram (módulo alerts.py):
  - boot do worker (silent)
  - resumo diário 09:00 BRT (silent)
  - publicação ok (com som)
  - publicação falhou (com som)
  - slot pulado por atraso (silent, 1x por slot)
  - runway < 7 dias (com som, 1x por dia)

Uso:
  python3 src/scheduler.py            # loop infinito
  python3 src/scheduler.py --once     # 1 passada e sai (útil pra testar)
  python3 src/scheduler.py --dry-run  # detecta slots devidos mas não publica
"""

from __future__ import annotations

import argparse
import csv
import html
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from alerts import notify

ROOT = Path(__file__).resolve().parent.parent
CALENDAR = ROOT / "content" / "calendar.csv"
PUBLISHED = ROOT / "output" / "published.csv"
SKIPPED_STATE = ROOT / "output" / ".skipped_slots.txt"
SUMMARY_STATE = ROOT / "output" / ".last_summary_date.txt"
INSIGHTS_COLLECT_STATE = ROOT / "output" / ".last_insights_collect.txt"

TZ = ZoneInfo("America/Sao_Paulo")
TICK_SECONDS = 60
MAX_STALENESS_MINUTES = 60  # slot atrasado mais que isso é pulado
RUNWAY_LOW_THRESHOLD_DAYS = 7
RUNWAY_AUTOGEN_THRESHOLD_DAYS = 14  # gatilho pra Fase B (autogen)
SUMMARY_HOUR = 9  # envia resumo diário no primeiro tick após 09:00 BRT
INSIGHTS_COLLECT_HOUR = 7  # coleta de insights 1x/dia, depois das 07h BRT


def parse_dt(value: str) -> datetime:
    """'2026-05-06 09:00' (hora local SP) → datetime tz-aware."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def _norm(post_id: str) -> str:
    """Normaliza '01' e '1' pra mesma chave; preserva 'reel_xxx'."""
    if post_id.startswith("reel_"):
        return post_id.lower()
    return post_id.lstrip("0") or "0"


def published_post_ids() -> set[str]:
    """IDs já publicados (lê schemas antigo e novo do published.csv)."""
    if not PUBLISHED.exists():
        return set()
    ids: set[str] = set()
    with PUBLISHED.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("post_id") or "").strip()
            if pid:
                ids.add(_norm(pid))
                continue
            # schema antigo: deduzir do campo `slides` (ex: "37.png, 37.2.png, …")
            slides = (row.get("slides") or "").strip()
            if slides:
                first = slides.split(",")[0].strip()
                stem = Path(first).stem
                if "." in stem:
                    stem = stem.split(".")[0]
                if stem:
                    ids.add(_norm(stem))
    return ids


def load_skipped() -> set[str]:
    """IDs já marcados como pulados (atraso >MAX_STALENESS_MINUTES)."""
    if not SKIPPED_STATE.exists():
        return set()
    return {
        line.strip()
        for line in SKIPPED_STATE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def mark_skipped(post_id: str) -> None:
    SKIPPED_STATE.parent.mkdir(parents=True, exist_ok=True)
    with SKIPPED_STATE.open("a", encoding="utf-8") as f:
        f.write(f"{_norm(post_id)}\n")


def load_calendar() -> list[dict]:
    if not CALENDAR.exists():
        sys.exit(f"calendar.csv não encontrado em {CALENDAR}")
    with CALENDAR.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_due_slots(now: datetime) -> list[dict]:
    """Retorna slots devidos (scheduled_at <= now) ainda não publicados."""
    done = published_post_ids()
    skipped = load_skipped()
    rows = load_calendar()
    due: list[dict] = []
    for row in rows:
        norm_id = _norm(row["post_id"])
        if norm_id in done or norm_id in skipped:
            continue
        when = parse_dt(row["scheduled_at"])
        if when > now:
            continue
        if now - when > timedelta(minutes=MAX_STALENESS_MINUTES):
            print(
                f"⚠ slot {row['slot']} ({row['post_id']}) atrasado "
                f">{MAX_STALENESS_MINUTES}min, pulando"
            )
            mark_skipped(row["post_id"])
            notify(
                f"⚠️ <b>Merge</b> · slot {html.escape(row['slot'])} "
                f"(<code>{html.escape(row['post_id'])}</code>) pulado por "
                f"atraso &gt;{MAX_STALENESS_MINUTES}min",
                silent=True,
            )
            continue
        due.append(row)
    due.sort(key=lambda r: parse_dt(r["scheduled_at"]))
    return due


def runway_info(now: datetime) -> dict:
    """Conta slots futuros pendentes e calcula dias restantes de inventário."""
    done = published_post_ids()
    skipped = load_skipped()
    rows = load_calendar()
    future: list[tuple[datetime, dict]] = []
    for row in rows:
        norm_id = _norm(row["post_id"])
        if norm_id in done or norm_id in skipped:
            continue
        when = parse_dt(row["scheduled_at"])
        if when <= now:
            continue
        future.append((when, row))
    if not future:
        return {"slots_pending": 0, "days_remaining": 0, "next_slot": None}
    future.sort()
    last_when, _ = future[-1]
    next_when, next_row = future[0]
    days_remaining = max(0, (last_when.date() - now.date()).days)
    return {
        "slots_pending": len(future),
        "days_remaining": days_remaining,
        "next_slot": (
            f"{next_row['post_id']} "
            f"({next_when.strftime('%d/%m %H:%M')})"
        ),
    }


def publish_slot(row: dict, dry_run: bool = False) -> tuple[bool, str]:
    """Roda src/publish.py pra esse slot. Retorna (ok, stderr)."""
    cmd = [sys.executable, str(ROOT / "src" / "publish.py"), "--post", row["post_id"]]
    if dry_run:
        cmd.append("--dry-run")
    print(
        f"→ slot {row['slot']} · {row['scheduled_at']} · "
        f"{row['format']} · {row['post_id']}"
    )
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode == 0, (proc.stderr or "").strip()


def maybe_daily_summary(now: datetime) -> None:
    """Manda resumo diário no primeiro tick após SUMMARY_HOUR cada dia."""
    today = now.date().isoformat()
    if SUMMARY_STATE.exists():
        if SUMMARY_STATE.read_text(encoding="utf-8").strip() == today:
            return
    if now.hour < SUMMARY_HOUR:
        return
    info = runway_info(now)
    runway_low = info["days_remaining"] < RUNWAY_LOW_THRESHOLD_DAYS
    icon = "🚨" if runway_low else "☀️"
    next_slot = info["next_slot"] or "—"
    msg = (
        f"{icon} <b>Merge · resumo {now.strftime('%d/%m')}</b>\n"
        f"runway: <b>{info['days_remaining']} dias</b> · "
        f"{info['slots_pending']} posts na fila\n"
        f"próximo: <code>{html.escape(next_slot)}</code>"
    )
    if runway_low:
        msg += (
            f"\n\n⚠️ runway abaixo de {RUNWAY_LOW_THRESHOLD_DAYS} dias — "
            f"hora de gerar mais conteúdo (Fase B)"
        )
    notify(msg, silent=not runway_low)
    SUMMARY_STATE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_STATE.write_text(today, encoding="utf-8")


def _maybe_collect_insights(now: datetime) -> None:
    """1x/dia, após INSIGHTS_COLLECT_HOUR, coleta insights da Graph API."""
    today = now.date().isoformat()
    if INSIGHTS_COLLECT_STATE.exists():
        if INSIGHTS_COLLECT_STATE.read_text(encoding="utf-8").strip() == today:
            return
    if now.hour < INSIGHTS_COLLECT_HOUR:
        return
    try:
        from insights.collector import collect_now
        n = collect_now()
        print(f"insights · snapshots coletados: {n}")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ insights collect erro: {e!r}")
        return
    INSIGHTS_COLLECT_STATE.parent.mkdir(parents=True, exist_ok=True)
    INSIGHTS_COLLECT_STATE.write_text(today, encoding="utf-8")


def _maybe_news_watch(now: datetime) -> None:
    try:
        from news.watcher import maybe_run as news_maybe_run
        news_maybe_run(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ news watcher erro: {e!r}")


def _maybe_stories(now: datetime) -> None:
    try:
        from news.stories import maybe_dispatch
        maybe_dispatch(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ stories dispatch erro: {e!r}")


def _maybe_insights_reports(now: datetime) -> None:
    try:
        from insights.reporter import maybe_daily_report, maybe_monthly_report
        maybe_daily_report(now)
        maybe_monthly_report(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ insights report erro: {e!r}")


def _maybe_autogen(now: datetime) -> None:
    """Gatilha geração de briefs quando runway < threshold."""
    info = runway_info(now)
    if info["days_remaining"] >= RUNWAY_AUTOGEN_THRESHOLD_DAYS:
        return
    try:
        from autogen.runner import maybe_generate_batch
        n = maybe_generate_batch()
        if n:
            print(f"autogen · {n} previews enviados (runway {info['days_remaining']}d)")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ autogen erro: {e!r}")


def tick(dry_run: bool = False) -> int:
    now = datetime.now(TZ)
    maybe_daily_summary(now)
    if not dry_run:
        _maybe_collect_insights(now)
        _maybe_insights_reports(now)
        _maybe_news_watch(now)
        _maybe_stories(now)
        _maybe_autogen(now)
    due = find_due_slots(now)
    if not due:
        return 0
    print(f"[{now.isoformat(timespec='seconds')}] {len(due)} slot(s) devido(s)")
    n_ok = 0
    for row in due:
        ok, err = publish_slot(row, dry_run=dry_run)
        if ok:
            n_ok += 1
            if not dry_run:
                notify(
                    f"✅ <b>Merge</b> · post "
                    f"<code>{html.escape(row['post_id'])}</code> publicado "
                    f"(slot {html.escape(row['slot'])} · "
                    f"{html.escape(row['format'])})"
                )
        else:
            print(f"✗ falha em {row['post_id']} — vai tentar de novo no próximo tick")
            tail = err[-300:] if err else "(sem stderr)"
            notify(
                f"❌ <b>Merge</b> · falha publicando "
                f"<code>{html.escape(row['post_id'])}</code>\n"
                f"<pre>{html.escape(tail)}</pre>"
            )
    return n_ok


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1 passada e sai")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print(f"Merge scheduler · TZ={TZ} · tick={TICK_SECONDS}s")
    print(f"  calendar : {CALENDAR}")
    print(f"  published: {PUBLISHED}")

    info = runway_info(datetime.now(TZ))
    notify(
        f"🚀 <b>Merge worker</b> iniciado\n"
        f"runway: <b>{info['days_remaining']} dias</b> · "
        f"{info['slots_pending']} posts na fila\n"
        f"próximo: <code>{html.escape(info['next_slot'] or '—')}</code>",
        silent=True,
    )

    if args.once:
        tick(dry_run=args.dry_run)
        return

    while True:
        try:
            tick(dry_run=args.dry_run)
        except SystemExit as e:
            print(f"⚠ subprocess exit: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ tick error: {e!r}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
