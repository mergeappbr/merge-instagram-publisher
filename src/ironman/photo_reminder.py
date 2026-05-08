"""
Reminder de fotos das provas.

Dispara alerta no Telegram quando uma race se aproxima do milestone T-30 e o
bg_pool tem POUCAS fotos específicas (i.e., a maioria são fotos genéricas
do banco _bank/ ou villarinho/marathon padrão).

Janela de alerta: T-35 a T-30 (5 dias antes do countdown disparar). Isso dá
tempo de subir fotos da prova antes do primeiro post de countdown.

Heurística "específica":
  - NÃO começa com 'villarinho_'  (banco genérico de endurance)
  - NÃO começa com '_bank/'        (banco genérico Pexels/Unsplash)
  - NÃO é 'marathon.jpg'           (silhueta urbana padrão)
  Tudo o resto conta como "específica" da prova.

State em output/.race_photo_reminders.json — dict {race_id: last_alert_iso_date}.
Evita re-alertar dentro de 7 dias da última notificação.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from alerts import notify

from .config import load_races, parse_race_date

ROOT = Path(__file__).resolve().parent.parent.parent
STATE_PATH = ROOT / "output" / ".race_photo_reminders.json"

# Janela em dias: alerta quando T-N estiver entre estes dois (inclusive).
WINDOW_MIN = 30
WINDOW_MAX = 35

# Threshold mínimo de fotos específicas no pool. Abaixo disso → alerta.
MIN_SPECIFIC = 4

# Cooldown entre alertas pra mesma race
COOLDOWN_DAYS = 7


def _is_specific(path: str) -> bool:
    """Filename é 'específico' da prova (não genérico/banco)?"""
    p = path.strip()
    if p.startswith("_bank/"):
        return False
    if p.startswith("villarinho_"):
        return False
    if p == "marathon.jpg":
        return False
    return True


def _count_specific(race: dict) -> int:
    pool = race.get("bg_pool") or []
    return sum(1 for p in pool if _is_specific(p))


def _load_state() -> dict[str, str]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _should_alert(race_id: str, today: date, state: dict[str, str]) -> bool:
    last = state.get(race_id)
    if not last:
        return True
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d").date()
    except ValueError:
        return True
    return (today - last_d) >= timedelta(days=COOLDOWN_DAYS)


def maybe_alert(now: datetime) -> int:
    """Verifica todas as races e dispara alerts pras que estão na janela e
    com bg_pool insuficiente. Retorna número de alerts disparados.

    Chamada idempotente: state evita re-spammar dentro de COOLDOWN_DAYS.
    """
    today = now.date()
    state = _load_state()
    sent = 0
    for race in load_races():
        try:
            race_d = parse_race_date(race["date"])
        except (ValueError, KeyError):
            continue
        days = (race_d - today).days
        if not (WINDOW_MIN <= days <= WINDOW_MAX):
            continue
        n_specific = _count_specific(race)
        if n_specific >= MIN_SPECIFIC:
            continue
        if not _should_alert(race["id"], today, state):
            continue
        # Dispara alerta
        race_name = race.get("name", race["id"])
        loc = race.get("location", "")
        target_folder = f"brand/images/<nome_da_foto>"
        msg = (
            f"📸 <b>Merge · fotos faltando · T-{days}</b>\n\n"
            f"<b>{html.escape(race_name)}</b> ({html.escape(loc)})\n"
            f"data: {race['date']}\n\n"
            f"pool atual: <b>{n_specific}</b> fotos específicas "
            f"(mínimo recomendado: {MIN_SPECIFIC})\n\n"
            f"⚠️ countdown T-30 dispara em <b>{days - WINDOW_MIN} dias</b>. "
            f"Sugiro subir {MIN_SPECIFIC - n_specific}+ fotos da prova em "
            f"<code>{target_folder}</code> e adicionar ao "
            f"<code>bg_pool</code> da race no <code>config/races.yml</code>.\n\n"
            f"Tipos sugeridos: largada, percurso pela cidade, atletas, "
            f"finish line, paisagem icônica."
        )
        notify(msg, force=True)
        state[race["id"]] = today.isoformat()
        sent += 1
    if sent:
        _save_state(state)
    return sent
