"""
Schedule Engine — Core business logic for IamBusy.

All schedule computation, timeline building, and status derivation
lives here, fully decoupled from Flask and the HTTP layer.
"""

from datetime import datetime, timedelta, time, date
from typing import Optional


# ───────────────────────────────────────────────── constants ──
DAYS: list[str] = [
    "Luni", "Marti", "Miercuri", "Joi", "Vineri", "Sambata", "Duminica"
]

COURSE_TYPE_LABELS: dict[str, str] = {
    "C": "Curs",
    "S": "Seminar",
    "L": "Laborator",
}


# ──────────────────────────────────────── schedule validation ──
def validate_schedule_entry(entry: tuple) -> bool:
    """Return True if *entry* is a valid (title, start, end) schedule tuple.

    A valid entry has:
      - exactly three string elements
      - start / end parseable as ``%H:%M``
      - end strictly after start
    """
    if not isinstance(entry, (tuple, list)) or len(entry) != 3:
        return False
    title, start_str, end_str = entry
    if not all(isinstance(s, str) for s in (title, start_str, end_str)):
        return False
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return False
    return end_time > start_time


def parse_time_safe(value: str) -> Optional[time]:
    """Parse an ``HH:MM`` string into a :class:`~datetime.time`, or *None*."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (ValueError, TypeError):
        return None


# ──────────────────────────────────── week parity computation ──
def is_odd_week(academic_start: date, check_date: Optional[date] = None) -> bool:
    """Determine whether *check_date* falls in an **odd** academic week.

    The academic calendar is zero-indexed from *academic_start*:
    week 0 → odd, week 1 → even, week 2 → odd …

    Parameters
    ----------
    academic_start:
        The Monday of the first academic week.
    check_date:
        Date to check.  Defaults to today.
    """
    if check_date is None:
        check_date = datetime.now().date()
    week_num = (check_date - academic_start).days // 7
    return week_num % 2 == 0


# ─────────────────────────────────────── timeline construction ──
def build_day_timeline(
    schedule: dict[str, list[tuple[str, str, str]]],
    target_date: Optional[date] = None,
) -> list[dict]:
    """Build an ordered list of time-blocks (courses + breaks) for a day.

    Each block is a dict with keys:
        ``type`` ("course" | "break"), ``title`` (str, courses only),
        ``start_dt``, ``end_dt`` (datetime).

    Parameters
    ----------
    schedule:
        A mapping of day-name → list of ``(title, HH:MM, HH:MM)`` tuples.
    target_date:
        The calendar date to build for.  Defaults to today.
    """
    if target_date is None:
        target_date = datetime.now().date()

    day_name = DAYS[target_date.weekday()]
    raw_blocks = schedule.get(day_name, [])

    # Parse into interval dicts
    intervals: list[dict] = []
    for entry in raw_blocks:
        if not validate_schedule_entry(entry):
            continue
        title, start_str, end_str = entry
        start_dt = datetime.combine(target_date, datetime.strptime(start_str, "%H:%M").time())
        end_dt = datetime.combine(target_date, datetime.strptime(end_str, "%H:%M").time())
        intervals.append({
            "type": "course",
            "title": title.strip(),
            "start_dt": start_dt,
            "end_dt": end_dt,
        })

    intervals.sort(key=lambda x: x["start_dt"])

    # Interleave break blocks
    full_timeline: list[dict] = []
    cursor = datetime.combine(target_date, time(0, 0))
    end_of_day = datetime.combine(target_date, time(23, 59))

    for iv in intervals:
        if iv["start_dt"] > cursor:
            full_timeline.append({
                "type": "break",
                "start_dt": cursor,
                "end_dt": iv["start_dt"],
            })
        full_timeline.append(iv)
        cursor = iv["end_dt"]

    if cursor < end_of_day:
        full_timeline.append({
            "type": "break",
            "start_dt": cursor,
            "end_dt": end_of_day,
        })

    return full_timeline


# ────────────────────────────────────────── status computation ──
def minutes_until(target_dt: datetime, now_dt: datetime) -> int:
    """Return the number of whole minutes remaining until *target_dt*.

    Returns 0 if *target_dt* is in the past.  Uses ceiling rounding so
    that "59 seconds left" reports as 1 minute.
    """
    delta = target_dt - now_dt
    seconds = max(0, int(delta.total_seconds()))
    return (seconds + 59) // 60  # ceiling


def compute_status(
    now_dt: datetime,
    timeline: list[dict],
    user_name: str = "Andrei",
) -> tuple[str, str, Optional[dict]]:
    """Derive a human-readable status from the current time and timeline.

    Returns
    -------
    (status_main, status_sub, current_block)
        *current_block* is *None* only when the timeline is empty.
    """
    if not timeline:
        return f"{user_name} nu are cursuri azi.", "", None

    current_block: Optional[dict] = None
    for blk in timeline:
        if blk["start_dt"] <= now_dt < blk["end_dt"]:
            current_block = blk
            break

    if current_block is None:
        current_block = timeline[0]

    end_str = current_block["end_dt"].strftime("%H:%M")
    minutes = minutes_until(current_block["end_dt"], now_dt)

    if current_block["type"] == "course":
        status_main = f"{user_name} are curs până la ora {end_str}."
    else:
        status_main = f"{user_name} e liber până la ora {end_str}."

    if minutes != 1:
        status_sub = f"Mai sunt {minutes} minute până atunci."
    else:
        status_sub = "Mai este 1 minut până atunci."

    return status_main, status_sub, current_block


# ──────────────────────────────── template data preparation ──
def parse_block_title(full_title: str) -> tuple[str, str]:
    """Split a block title into (subject, room).

    Titles follow the convention ``"Subject (Type) | Room"``.
    If no ``|`` is present, *room* is an empty string.
    """
    if " | " in full_title:
        subject, room = full_title.split(" | ", 1)
        return subject.strip(), room.strip()
    return full_title.strip(), ""


def prepare_blocks_for_ui(
    timeline: list[dict],
    current_block: Optional[dict],
) -> list[dict]:
    """Transform raw timeline blocks into template-ready dicts.

    Each returned dict has keys:
        ``type``, ``title``, ``subject``, ``room``, ``start``, ``end``,
        ``is_current``.
    """
    blocks: list[dict] = []
    for blk in timeline:
        full_title = blk.get("title", "")
        subject, room = parse_block_title(full_title)
        blocks.append({
            "type": blk["type"],
            "title": full_title,
            "subject": subject,
            "room": room,
            "start": blk["start_dt"].strftime("%H:%M"),
            "end": blk["end_dt"].strftime("%H:%M"),
            "is_current": blk is current_block,
        })
    return blocks
