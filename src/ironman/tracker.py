"""
Tracker das provas — cron diário.

Roda 1x/dia (idempotente via .last_ironman_tracker.txt). Pra cada race em
config/races.yml:

  · countdown: dias até a prova ∈ {30, 15, 7, 1} e ainda não enviado
    → dispatch_countdown(race, days)
  · results:   dias após a prova == 1 e kind=ironman e ainda não enviado
    → tenta results.try_fetch(race); se vier dado, dispatch_results;
      senão, notifica Pedro pra colar manualmente (free-text "Ajustar")

State em output/.ironman_milestones.json:
  { "<race_id>:t30": "2026-05-01", ... }   (ISO date do dia que disparou)

Modos de execução:

  python -m ironman.tracker                       # dry-run preview do que dispararia hoje
  python -m ironman.tracker --run                 # roda de verdade
  python -m ironman.tracker --force <race_id>     # gera countdown com dias atuais
                                                    (útil pra testar antes de milestone)
  python -m ironman.tracker --force-results <id>  # tenta resultados de uma race ironman
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config as cfg
from . import results as results_mod
from . import runner

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = ROOT / "output" / ".ironman_milestones.json"
DAILY_STATE = ROOT / "output" / ".last_ironman_tracker.txt"

TZ = ZoneInfo("America/Sao_Paulo")
COUNTDOWN_MILESTONES = (30, 15, 7, 1)
RUN_HOUR = 9  # roda 1x/dia, primeiro tick após 09:00 BRT


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _milestone_key(race_id: str, kind: str) -> str:
    return f"{race_id}:{kind}"


def _already_sent(state: dict, race_id: str, kind: str) -> bool:
    return _milestone_key(race_id, kind) in state


def _mark_sent(state: dict, race_id: str, kind: str, today: date) -> None:
    state[_milestone_key(race_id, kind)] = today.isoformat()
    _save_state(state)


def run_today(today: date | None = None, dry_run: bool = False) -> int:
    today = today or datetime.now(TZ).date()
    races = cfg.load_races()
    state = _load_state()
    fired = 0

    for race in races:
        rid = race["id"]
        d_until = cfg.days_until(race, today)
        d_after = cfg.days_after(race, today)

        # Countdown
        for m in COUNTDOWN_MILESTONES:
            if d_until != m:
                continue
            if _already_sent(state, rid, f"t{m}"):
                continue
            print(f"tracker · {rid} · T-{m}")
            if dry_run:
                fired += 1
                continue
            # Countdown automático pra todas as kinds (ironman, mtb, trail) —
            # template renderiza logo card quando race tem `logo`, ou pill com
            # sigla quando não tem. Caption usa prompt específico do kind.
            ok = runner.dispatch_countdown(race, m)
            if ok:
                _mark_sent(state, rid, f"t{m}", today)
                fired += 1

        # Results T+1 (só ironman)
        if race.get("kind") == "ironman" and d_after == 1:
            if not _already_sent(state, rid, "tplus1"):
                print(f"tracker · {rid} · T+1 results")
                if dry_run:
                    fired += 1
                else:
                    fired += _dispatch_results_or_notify(race, state, today)

    return fired


def _dispatch_results_or_notify(race: dict, state: dict, today: date) -> int:
    """Tenta fetch automático; se falhar, notifica Pedro pra colar manual."""
    from alerts import notify
    import html as _html

    res = results_mod.try_fetch(race)
    if res is None:
        notify(
            f"🏁 <b>[Ironman · T+1] {_html.escape(race.get('name','?'))}</b>\n"
            f"prova foi ontem — sem fetch automático ainda.\n\n"
            f"Cole o top 10 M+F via /pending ou em qualquer mensagem assim:\n"
            f"<pre>masculino:\n1. Nome 08:23:11\n... 10 linhas\nfeminino:\n1. ...</pre>"
        )
        # Não marcamos como sent — tentaremos amanhã também (até alguém colar).
        # Anti-spam: marcamos um sub-key pra não spammar várias vezes no mesmo dia.
        anti_spam_key = f"tplus1_pending_{today.isoformat()}"
        if state.get(_milestone_key(race["id"], "_anti_spam")) == anti_spam_key:
            return 0
        state[_milestone_key(race["id"], "_anti_spam")] = anti_spam_key
        _save_state(state)
        return 0
    ok = runner.dispatch_results(race, res)
    if ok:
        _mark_sent(state, race["id"], "tplus1", today)
        return 1
    return 0


def maybe_run(now: datetime) -> bool:
    """Hook do scheduler — 1x/dia depois das 09h BRT."""
    today = now.date().isoformat()
    if DAILY_STATE.exists():
        if DAILY_STATE.read_text(encoding="utf-8").strip() == today:
            return False
    if now.hour < RUN_HOUR:
        return False
    fired = run_today(now.date(), dry_run=False)
    DAILY_STATE.parent.mkdir(parents=True, exist_ok=True)
    DAILY_STATE.write_text(today, encoding="utf-8")
    if fired:
        print(f"tracker · {fired} milestone(s) disparado(s)")
    return True


def force_countdown(race_id: str) -> int:
    """Gera countdown pra uma race com os dias atuais (independente de milestone).
    Não marca state — útil pra testar."""
    races = cfg.load_races()
    race = next((r for r in races if r["id"] == race_id), None)
    if race is None:
        print(f"⚠ race {race_id!r} não encontrada")
        return 1
    today = datetime.now(TZ).date()
    days = cfg.days_until(race, today)
    if days <= 0:
        print(f"⚠ race {race_id} já aconteceu (d_until={days})")
        return 1
    print(f"force · {race_id} · T-{days}")
    runner.dispatch_countdown(race, days)
    return 0


def force_results(race_id: str) -> int:
    races = cfg.load_races()
    race = next((r for r in races if r["id"] == race_id), None)
    if race is None or race.get("kind") != "ironman":
        print(f"⚠ race {race_id!r} não encontrada ou não é ironman")
        return 1
    res = results_mod.try_fetch(race)
    if res is None:
        print("⚠ try_fetch retornou None — implementação ainda é stub.")
        print("   Use o flow do Telegram (notify + ajuste manual).")
        return 1
    runner.dispatch_results(race, res)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true")
    p.add_argument("--force", metavar="RACE_ID", help="gera countdown com dias atuais")
    p.add_argument("--force-results", metavar="RACE_ID", help="tenta gerar resultados")
    args = p.parse_args(argv)

    if args.force:
        return force_countdown(args.force)
    if args.force_results:
        return force_results(args.force_results)
    if args.run:
        run_today(dry_run=False)
        return 0
    # default: dry-run
    n = run_today(dry_run=True)
    print(f"dry-run · {n} milestone(s) disparariam hoje")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    sys.exit(main())
