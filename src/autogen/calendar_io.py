"""
Leitura/escrita do content/calendar.csv. Encontra próximos slots livres,
adiciona linhas mantendo formato existente, calcula runway.

Calendar schema: slot,scheduled_at,format,post_id,theme,note
Cadência fixa: 09:00 e 19:00 BRT, todos os dias.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CALENDAR = ROOT / "content" / "calendar.csv"
PUBLISHED = ROOT / "output" / "published.csv"
SKIPPED_STATE = ROOT / "output" / ".skipped_slots.txt"

CAL_FIELDS = ["slot", "scheduled_at", "format", "post_id", "theme", "note"]
SLOT_HOURS = (9, 19)  # 09:00 e 19:00 BRT


def _norm(post_id: str) -> str:
    if post_id.startswith("reel_"):
        return post_id.lower()
    return post_id.lstrip("0") or "0"


def load_calendar() -> list[dict]:
    if not CALENDAR.exists():
        return []
    with CALENDAR.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_calendar(rows: list[dict]) -> None:
    CALENDAR.parent.mkdir(parents=True, exist_ok=True)
    with CALENDAR.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAL_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CAL_FIELDS})


def _published_ids() -> set[str]:
    if not PUBLISHED.exists():
        return set()
    out: set[str] = set()
    with PUBLISHED.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = (row.get("post_id") or "").strip()
            if pid:
                out.add(_norm(pid))
    return out


def _skipped_ids() -> set[str]:
    if not SKIPPED_STATE.exists():
        return set()
    return {
        line.strip()
        for line in SKIPPED_STATE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def runway_days(now: datetime | None = None) -> int:
    """Quantos dias de inventário restam (slots futuros não publicados)."""
    now = now or datetime.now()
    rows = load_calendar()
    done = _published_ids()
    skipped = _skipped_ids()
    futures: list[datetime] = []
    for r in rows:
        if _norm(r.get("post_id", "")) in done | skipped:
            continue
        try:
            when = datetime.strptime(r["scheduled_at"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if when <= now:
            continue
        futures.append(when)
    if not futures:
        return 0
    return max(0, (max(futures).date() - now.date()).days)


def next_free_slots(n: int, *, now: datetime | None = None) -> list[datetime]:
    """
    Retorna n próximos slots (horários 09/19) ainda não usados no calendar.csv.
    Olha após o último scheduled_at existente; se calendar vazio, começa hoje.
    """
    now = now or datetime.now()
    rows = load_calendar()
    used: set[str] = set()
    last_when: datetime | None = None
    for r in rows:
        try:
            when = datetime.strptime(r["scheduled_at"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        used.add(when.strftime("%Y-%m-%d %H:%M"))
        if last_when is None or when > last_when:
            last_when = when

    cursor = last_when or now.replace(hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0)
    if last_when is None and now.hour >= SLOT_HOURS[1]:
        cursor = (now + timedelta(days=1)).replace(hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0)

    free: list[datetime] = []
    while len(free) < n:
        # avança cursor pro próximo slot oficial
        if cursor.hour < SLOT_HOURS[0]:
            cursor = cursor.replace(hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0)
        elif cursor.hour < SLOT_HOURS[1]:
            cursor = cursor.replace(hour=SLOT_HOURS[1], minute=0, second=0, microsecond=0)
        else:
            cursor = (cursor + timedelta(days=1)).replace(
                hour=SLOT_HOURS[0], minute=0, second=0, microsecond=0
            )
        key = cursor.strftime("%Y-%m-%d %H:%M")
        if key not in used and cursor > now:
            free.append(cursor)
            used.add(key)
        # incrementa cursor pro próximo loop
        if cursor.hour == SLOT_HOURS[0]:
            cursor = cursor.replace(hour=SLOT_HOURS[1])
        else:
            cursor = (cursor + timedelta(days=1)).replace(hour=SLOT_HOURS[0])
    return free


def next_slot_index() -> int:
    rows = load_calendar()
    if not rows:
        return 1
    nums: list[int] = []
    for r in rows:
        try:
            nums.append(int(r.get("slot", 0)))
        except ValueError:
            continue
    return (max(nums) if nums else 0) + 1


def append_calendar_rows(new_rows: list[dict]) -> None:
    """Append linhas no calendar.csv mantendo header."""
    rows = load_calendar()
    rows.extend(new_rows)
    save_calendar(rows)


def insert_at_first_free(
    *,
    post_id: str,
    fmt: str,
    theme: str,
    note: str,
    when: datetime | None = None,
) -> dict:
    """Insere uma linha no calendar.csv (próximo slot livre se when=None)."""
    rows = load_calendar()
    idx = next_slot_index()
    if when is None:
        when = next_free_slots(1)[0]
    new = {
        "slot": str(idx),
        "scheduled_at": when.strftime("%Y-%m-%d %H:%M"),
        "format": fmt,
        "post_id": post_id,
        "theme": theme,
        "note": note,
    }
    rows.append(new)
    save_calendar(rows)
    return new
