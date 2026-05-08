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
import json
import subprocess
import sys
import threading
import time
import traceback
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
HOURLY_NEWS_STATE = ROOT / "output" / ".last_hourly_news_report.txt"
SLOT_FAILURES_STATE = ROOT / "output" / ".slot_failures.json"

TZ = ZoneInfo("America/Sao_Paulo")
TICK_SECONDS = 60
MAX_STALENESS_MINUTES = 60  # slot atrasado mais que isso é pulado
RUNWAY_LOW_THRESHOLD_DAYS = 7
RUNWAY_AUTOGEN_THRESHOLD_DAYS = 14  # gatilho pra Fase B (autogen)
SUMMARY_HOUR = 9  # envia resumo diário no primeiro tick após 09:00 BRT
INSIGHTS_COLLECT_HOUR = 7  # coleta de insights 1x/dia, depois das 07h BRT
MAX_SLOT_FAILURES = 3  # após N falhas consecutivas, slot é pausado pra revisão


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


def _load_slot_failures() -> dict[str, int]:
    """{post_id_norm: consecutive_failures}. Ausente = 0."""
    if not SLOT_FAILURES_STATE.exists():
        return {}
    try:
        data = json.loads(SLOT_FAILURES_STATE.read_text(encoding="utf-8"))
        return {k: int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_slot_failures(state: dict[str, int]) -> None:
    SLOT_FAILURES_STATE.parent.mkdir(parents=True, exist_ok=True)
    SLOT_FAILURES_STATE.write_text(json.dumps(state), encoding="utf-8")


def record_slot_failure(post_id: str) -> int:
    """Incrementa contador de falhas; retorna o novo total."""
    state = _load_slot_failures()
    key = _norm(post_id)
    state[key] = state.get(key, 0) + 1
    _save_slot_failures(state)
    return state[key]


def reset_slot_failure(post_id: str) -> None:
    state = _load_slot_failures()
    key = _norm(post_id)
    if key in state:
        state.pop(key)
        _save_slot_failures(state)


def slot_paused(post_id: str) -> bool:
    """True se slot já estourou MAX_SLOT_FAILURES — não tentar de novo."""
    state = _load_slot_failures()
    return state.get(_norm(post_id), 0) >= MAX_SLOT_FAILURES


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
        if slot_paused(row["post_id"]):
            # já alertamos quando estourou; agora silencia até reset manual
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
    notify(msg, silent=not runway_low, force=True)
    SUMMARY_STATE.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_STATE.write_text(today, encoding="utf-8")


def maybe_hourly_news_report(now: datetime) -> None:
    """Manda ranking horário das news mais quentes do pool (texto + botão
    'Produzir' por item). Dispara 1x por hora-cheia entre 8h e 22h BRT.
    State file guarda 'YYYY-MM-DDTHH' da última hora enviada."""
    if now.hour < 8 or now.hour > 22:
        return
    stamp = now.strftime("%Y-%m-%dT%H")
    if HOURLY_NEWS_STATE.exists():
        if HOURLY_NEWS_STATE.read_text(encoding="utf-8").strip() == stamp:
            return
    try:
        from bot.handlers import _send_hourly_news_ranking
        _send_hourly_news_ranking(chat_id=None)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ hourly news report erro: {e!r}")
        return
    HOURLY_NEWS_STATE.parent.mkdir(parents=True, exist_ok=True)
    HOURLY_NEWS_STATE.write_text(stamp, encoding="utf-8")


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


def _maybe_news_feed(now: datetime) -> None:
    """Feed news 2x/dia (08h/14h BRT) — pega top item do pool e gera feed
    post via writer+reviewer. Roda ANTES de stories pra reservar o item top."""
    try:
        from news.feed_post import maybe_dispatch as feed_news_dispatch
        feed_news_dispatch(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ news feed dispatch erro: {e!r}")


def _maybe_insights_reports(now: datetime) -> None:
    try:
        from insights.reporter import maybe_daily_report, maybe_monthly_report
        maybe_daily_report(now)
        maybe_monthly_report(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ insights report erro: {e!r}")


def _maybe_competitors_digest(now: datetime) -> None:
    """Sexta 16h BRT — digest semanal de concorrentes."""
    try:
        from competitors.digest import maybe_run as competitors_maybe_run
        competitors_maybe_run(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ competitors digest erro: {e!r}")


def _maybe_ironman_tracker(now: datetime) -> None:
    """Diário 09h BRT — checa milestones T-30/-15/-7/-1 e T+1 de races.yml."""
    try:
        from ironman.tracker import maybe_run as ironman_maybe_run
        ironman_maybe_run(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ ironman tracker erro: {e!r}")


def _maybe_photo_reminder(now: datetime) -> None:
    """Diário ~09h BRT — alerta sobre races T-35..T-30 com bg_pool insuficiente
    de fotos específicas, pra dar tempo de subir fotos antes do countdown.
    Cooldown interno (7d) evita spam."""
    if now.hour < SUMMARY_HOUR:
        return
    try:
        from ironman.photo_reminder import maybe_alert
        n = maybe_alert(now)
        if n:
            print(f"photo_reminder · {n} alert(s) enviado(s)")
    except Exception as e:  # noqa: BLE001
        print(f"⚠ photo reminder erro: {e!r}")


def _maybe_r2_cleanup(now: datetime) -> None:
    """Diário — apaga objetos R2 antigos pra ficar dentro do free tier (10GB)."""
    try:
        from r2_cleanup import maybe_run as r2_cleanup_maybe_run
        r2_cleanup_maybe_run(now)
    except Exception as e:  # noqa: BLE001
        print(f"⚠ r2_cleanup erro: {e!r}")


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
        maybe_hourly_news_report(now)
        _maybe_collect_insights(now)
        _maybe_insights_reports(now)
        _maybe_news_watch(now)
        _maybe_news_feed(now)   # antes de stories pra reservar item top
        _maybe_stories(now)
        _maybe_competitors_digest(now)
        _maybe_ironman_tracker(now)
        _maybe_photo_reminder(now)
        _maybe_r2_cleanup(now)
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
                reset_slot_failure(row["post_id"])
                notify(
                    f"✅ <b>Merge</b> · post "
                    f"<code>{html.escape(row['post_id'])}</code> publicado "
                    f"(slot {html.escape(row['slot'])} · "
                    f"{html.escape(row['format'])})"
                )
        else:
            failures = record_slot_failure(row["post_id"])
            tail = err[-300:] if err else "(sem stderr)"
            print(
                f"✗ falha em {row['post_id']} ({failures}/{MAX_SLOT_FAILURES})"
            )
            if failures >= MAX_SLOT_FAILURES:
                # alerta UMA vez quando estoura — força bypass do dedupe
                notify(
                    f"🛑 <b>Merge</b> · slot "
                    f"<code>{html.escape(row['post_id'])}</code> "
                    f"<b>pausado</b> após {failures} falhas consecutivas.\n"
                    f"Revise manualmente e remova "
                    f"<code>output/.slot_failures.json</code> pra retomar.\n"
                    f"<pre>{html.escape(tail)}</pre>",
                    force=True,
                )
            else:
                # falhas intermediárias: dedupe_key estável por post_id pra
                # não spammar mesmo que o stderr (que contém timestamp da
                # pasta de upload) mude a cada tick.
                notify(
                    f"❌ <b>Merge</b> · falha publicando "
                    f"<code>{html.escape(row['post_id'])}</code> "
                    f"({failures}/{MAX_SLOT_FAILURES})\n"
                    f"<pre>{html.escape(tail)}</pre>",
                    dedupe_key=f"slot_fail:{_norm(row['post_id'])}",
                )
    return n_ok


def _start_bot_thread() -> None:
    """Sobe o long-polling do bot Telegram numa thread daemon — mesmo processo,
    mesmo volume montado. Falha aqui não derruba o scheduler (só loga)."""
    def _run() -> None:
        try:
            from bot.poller import main as bot_main
            bot_main()
        except SystemExit as e:
            print(f"⚠ bot encerrou: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ bot crashed: {e!r}")
            traceback.print_exc()
    t = threading.Thread(target=_run, name="merge-bot", daemon=True)
    t.start()
    print("scheduler · bot thread iniciada")


def _validate_env() -> list[str]:
    """Checa envs críticas. Retorna lista de problemas (vazia = ok).

    Pares OR (R2_PUBLIC_BASE_URL ou R2_PUBLIC_BASE) — basta uma das duas.
    """
    import os

    required = [
        "META_GRAPH_ACCESS_TOKEN",
        "IG_BUSINESS_ACCOUNT_ID",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ANTHROPIC_API_KEY_MERGE",
    ]
    or_groups = [("R2_PUBLIC_BASE_URL", "R2_PUBLIC_BASE")]

    problems: list[str] = []
    for key in required:
        if not os.environ.get(key, "").strip():
            problems.append(key)
    for group in or_groups:
        if not any(os.environ.get(k, "").strip() for k in group):
            problems.append(" OU ".join(group))
    return problems


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1 passada e sai")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-bot", action="store_true", help="não sobe a thread do bot")
    args = parser.parse_args(argv)

    print(f"Merge scheduler · TZ={TZ} · tick={TICK_SECONDS}s")
    print(f"  calendar : {CALENDAR}")
    print(f"  published: {PUBLISHED}")

    env_problems = _validate_env()
    if env_problems:
        msg_console = "ENV CRÍTICAS faltando: " + ", ".join(env_problems)
        print(f"⚠ {msg_console}")
        notify(
            "🚨 <b>Merge worker · ENVS FALTANDO</b>\n"
            "Publicações vão falhar até corrigir no Railway:\n"
            "<pre>" + html.escape("\n".join(f"- {p}" for p in env_problems)) + "</pre>"
        )

    if not args.once and not args.dry_run and not args.no_bot:
        _start_bot_thread()

    # Startup boot é silencioso — Railway reinicia muito (deploys, restarts).
    # Notificação 1x/dia já vem via maybe_daily_summary; erros via notify().
    print(
        f"🚀 worker iniciado · runway {runway_info(datetime.now(TZ))['days_remaining']}d"
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
