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
Slots com mais de MAX_STALENESS_MINUTES de atraso são pulados (com warning).

Uso:
  python3 src/scheduler.py            # loop infinito
  python3 src/scheduler.py --once     # 1 passada e sai (útil pra testar)
  python3 src/scheduler.py --dry-run  # detecta slots devidos mas não publica
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CALENDAR = ROOT / "content" / "calendar.csv"
PUBLISHED = ROOT / "output" / "published.csv"

TZ = ZoneInfo("America/Sao_Paulo")
TICK_SECONDS = 60
MAX_STALENESS_MINUTES = 60  # slot atrasado mais que isso é pulado


def parse_dt(value: str) -> datetime:
    """'2026-05-06 09:00' (hora local SP) → datetime tz-aware."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


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


def _norm(post_id: str) -> str:
    """Normaliza '01' e '1' pra mesma chave; preserva 'reel_xxx'."""
    if post_id.startswith("reel_"):
        return post_id.lower()
    return post_id.lstrip("0") or "0"


def load_calendar() -> list[dict]:
    if not CALENDAR.exists():
        sys.exit(f"calendar.csv não encontrado em {CALENDAR}")
    with CALENDAR.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_due_slots(now: datetime) -> list[dict]:
    """Retorna slots devidos (scheduled_at <= now) ainda não publicados."""
    done = published_post_ids()
    rows = load_calendar()
    due: list[dict] = []
    for row in rows:
        if _norm(row["post_id"]) in done:
            continue
        when = parse_dt(row["scheduled_at"])
        if when > now:
            continue
        # filtra atrasos absurdos
        if now - when > timedelta(minutes=MAX_STALENESS_MINUTES):
            print(f"⚠ slot {row['slot']} ({row['post_id']}) atrasado >{MAX_STALENESS_MINUTES}min, pulando")
            done.add(_norm(row["post_id"]))  # evita re-warning
            continue
        due.append(row)
    # publicar em ordem cronológica
    due.sort(key=lambda r: parse_dt(r["scheduled_at"]))
    return due


def publish_slot(row: dict, dry_run: bool = False) -> bool:
    cmd = [sys.executable, str(ROOT / "src" / "publish.py"), "--post", row["post_id"]]
    if dry_run:
        cmd.append("--dry-run")
    print(f"→ slot {row['slot']} · {row['scheduled_at']} · {row['format']} · {row['post_id']}")
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode == 0


def tick(dry_run: bool = False) -> int:
    now = datetime.now(TZ)
    due = find_due_slots(now)
    if not due:
        return 0
    print(f"[{now.isoformat(timespec='seconds')}] {len(due)} slot(s) devido(s)")
    n_ok = 0
    for row in due:
        if publish_slot(row, dry_run=dry_run):
            n_ok += 1
        else:
            print(f"✗ falha em {row['post_id']} — vai tentar de novo no próximo tick")
            # NÃO interrompe o loop: se um post falha, o próximo segue
    return n_ok


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="1 passada e sai")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    print(f"Merge scheduler · TZ={TZ} · tick={TICK_SECONDS}s")
    print(f"  calendar : {CALENDAR}")
    print(f"  published: {PUBLISHED}")

    if args.once:
        tick(dry_run=args.dry_run)
        return

    while True:
        try:
            tick(dry_run=args.dry_run)
        except SystemExit as e:
            # publish.py chama sys.exit em erro — não derruba o scheduler
            print(f"⚠ subprocess exit: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ tick error: {e!r}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
