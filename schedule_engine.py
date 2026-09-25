"""
Schedule Engine — Core business logic for IamBusy.

All schedule computation, timeline building, and status derivation
lives here, fully decoupled from Flask and the HTTP layer.
"""

import re
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

# Display names with diacritics (DB keys stay ASCII).
DAY_DISPLAY: dict[str, str] = {
    "Luni": "Luni", "Marti": "Marți", "Miercuri": "Miercuri", "Joi": "Joi",
    "Vineri": "Vineri", "Sambata": "Sâmbătă", "Duminica": "Duminică",
}

DAY_SHORT: list[str] = ["L", "Ma", "Mi", "J", "V", "S", "D"]

MONTHS_SHORT: list[str] = [
    "ian", "feb", "mar", "apr", "mai", "iun",
    "iul", "aug", "sep", "oct", "nov", "dec",
]

MONTHS_LONG: list[str] = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]


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
    # Snap both dates to their Monday so that the entire week shares the
    # same parity — prevents the odd/even flag from flipping mid-week.
    monday_of_check = check_date - timedelta(days=check_date.weekday())
    monday_of_start = academic_start - timedelta(days=academic_start.weekday())
    week_num = (monday_of_check - monday_of_start).days // 7
    return week_num % 2 == 0


def academic_week_number(academic_start: date, check_date: date) -> int:
    """Return the 1-based academic week of *check_date* (<= 0 before start)."""
    monday_of_check = check_date - timedelta(days=check_date.weekday())
    monday_of_start = academic_start - timedelta(days=academic_start.weekday())
    return (monday_of_check - monday_of_start).days // 7 + 1


def format_date_ro(d: date, long: bool = False) -> str:
    """Format *d* as e.g. ``28 sep`` (or ``28 septembrie`` when *long*)."""
    months = MONTHS_LONG if long else MONTHS_SHORT
    return f"{d.day} {months[d.month - 1]}"


def relative_day_ro(d: date, today: date) -> str:
    """Return ``azi`` / ``mâine`` or ``luni, 28 sep`` relative to *today*."""
    delta = (d - today).days
    if delta == 0:
        return "azi"
    if delta == 1:
        return "mâine"
    return f"{DAY_DISPLAY[DAYS[d.weekday()]].lower()}, {format_date_ro(d)}"


# ────────────────────────── next-activity look-ahead ──
def find_next_activity(
    schedule_odd: dict[str, list[tuple[str, str, str]]],
    schedule_even: dict[str, list[tuple[str, str, str]]],
    academic_start: date,
    from_date: date,
) -> Optional[dict]:
    """Find the first course on the next workday after *from_date*.

    Skips weekends and any day before the academic year starts.

    Returns
    -------
    ``{"day_name": str, "date": date, "start_time": str}`` or *None*
    if no courses are found within the next 14 days (counted from the
    later of *from_date* and the day before *academic_start*).
    """
    base = max(from_date, academic_start - timedelta(days=1))
    for offset in range(1, 15):  # look up to two weeks ahead
        candidate = base + timedelta(days=offset)
        weekday = candidate.weekday()  # 0=Mon … 6=Sun

        # Skip Saturday (5) and Sunday (6)
        if weekday >= 5:
            continue

        odd = is_odd_week(academic_start, candidate)
        schedule = schedule_odd if odd else schedule_even
        day_name = DAYS[weekday]
        entries = schedule.get(day_name, [])

        # Find the earliest valid course
        earliest_start: Optional[str] = None
        for entry in entries:
            if not validate_schedule_entry(entry):
                continue
            _, start_str, _ = entry
            if earliest_start is None or start_str < earliest_start:
                earliest_start = start_str

        if earliest_start:
            return {
                "day_name": day_name,
                "date": candidate,
                "start_time": earliest_start,
            }

    return None


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
    next_activity: Optional[dict] = None,
) -> tuple[str, str, Optional[dict]]:
    """Derive a human-readable status from the current time and timeline.

    Parameters
    ----------
    next_activity:
        Result of :func:`find_next_activity`, used when today's courses
        are finished to show when the next day's activity starts.

    Returns
    -------
    (status_main, status_sub, current_block)
        *current_block* is *None* only when the timeline is empty.
    """
    # ── helper: build a "free until next day" message ──
    def _free_until_next(na: Optional[dict]) -> tuple[str, str]:
        if na is None:
            return f"{user_name} e liber.", ""
        when = relative_day_ro(na["date"], now_dt.date())
        return (
            f"{user_name} e liber până {when}, la {na['start_time']}.",
            "",
        )

    # No courses at all today
    if not timeline:
        main, sub = _free_until_next(next_activity)
        return main, sub, None

    # Collect only the course blocks
    courses = [b for b in timeline if b["type"] == "course"]

    # If there are no courses (only breaks), treat as free day
    if not courses:
        main, sub = _free_until_next(next_activity)
        return main, sub, None

    # Check if all courses are finished
    last_course_end = max(c["end_dt"] for c in courses)
    if now_dt >= last_course_end:
        # Today's schedule is done — show next activity
        main, sub = _free_until_next(next_activity)
        # Current block is the trailing break
        current_block = None
        for blk in timeline:
            if blk["start_dt"] <= now_dt < blk["end_dt"]:
                current_block = blk
                break
        if current_block is None:
            current_block = timeline[-1]
        return main, sub, current_block

    # Normal case: find which block we are in right now
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

    status_sub = f"Mai sunt {format_duration(minutes)}."

    return status_main, status_sub, current_block


def format_duration(minutes: int) -> str:
    """Format a minute count as ``45 min`` / ``2h`` / ``1h 30min``."""
    hours, mins = divmod(max(0, minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}min"
    if hours:
        return f"{hours}h"
    return f"{mins} min"


# ──────────────────────────────── template data preparation ──
def split_course_type(subject: str) -> tuple[str, str]:
    """Split ``"OOP (L)"`` into ``("OOP", "L")``; type is ``""`` if absent."""
    m = re.match(r"^(.*?)\s*\(([CSL])\)\s*$", subject)
    if m:
        return m.group(1), m.group(2)
    return subject, ""


def parse_block_title(full_title: str) -> tuple[str, str]:
    """Split a block title into (subject, room).

    Titles follow the convention ``"Subject (Type) | Room"``.
    If no ``|`` is present, *room* is an empty string.
    """
    if " | " in full_title:
        subject, room = full_title.split(" | ", 1)
        return subject.strip(), room.strip()
    return full_title.strip(), ""


def _hhmm_to_minutes(value: str) -> int:
    """Convert ``HH:MM`` to minutes since midnight (0 on parse failure)."""
    t = parse_time_safe(value)
    return t.hour * 60 + t.minute if t else 0


def _minutes_to_hhmm(total: int) -> str:
    total = max(0, min(total, 23 * 60 + 59))
    return f"{total // 60:02d}:{total % 60:02d}"


def busy_segments(
    entries: list[tuple[str, str, str]],
    window_start_hour: int = 7,
    window_end_hour: int = 22,
) -> list[dict]:
    """Return course intervals as percentages of a daily hour window.

    Used to draw the tiny "busy bar" under each day in the week strip and
    the month calendar.  Each segment is ``{"left": %, "width": %, "kind"}``.
    """
    w0 = window_start_hour * 60
    w1 = window_end_hour * 60
    span = w1 - w0
    segments: list[dict] = []
    for entry in entries:
        if not validate_schedule_entry(entry):
            continue
        title, start_str, end_str = entry
        s = max(w0, _hhmm_to_minutes(start_str))
        e = min(w1, _hhmm_to_minutes(end_str))
        if e <= s:
            continue
        subject, _room = parse_block_title(title)
        _subj, kind = split_course_type(subject)
        segments.append({
            "left": round((s - w0) / span * 100, 2),
            "width": round((e - s) / span * 100, 2),
            "kind": kind or "x",
        })
    return segments


def free_windows(timeline: list[dict], min_minutes: int = 30) -> list[dict]:
    """Return the gaps *between* courses that last at least *min_minutes*.

    Leading / trailing free time is excluded — it is implied by the first
    and last course.  Each window is ``{"start", "end", "duration"}``.
    """
    courses = [b for b in timeline if b["type"] == "course"]
    if len(courses) < 2:
        return []
    windows: list[dict] = []
    for prev, nxt in zip(courses, courses[1:]):
        gap = int((nxt["start_dt"] - prev["end_dt"]).total_seconds() // 60)
        if gap >= min_minutes:
            windows.append({
                "start": prev["end_dt"].strftime("%H:%M"),
                "end": nxt["start_dt"].strftime("%H:%M"),
                "duration": format_duration(gap),
            })
    return windows


def day_summary(
    schedule: dict[str, list[tuple[str, str, str]]],
    target_date: date,
    academic_start: date,
) -> dict:
    """Compact JSON-ready description of one calendar day.

    Used by the month calendar so a user can see, before picking a date,
    how busy that day already is.
    """
    day_name = DAYS[target_date.weekday()]
    entries = schedule.get(day_name, [])
    activities: list[dict] = []
    for entry in sorted(entries, key=lambda e: e[1]):
        if not validate_schedule_entry(entry):
            continue
        title, start_str, end_str = entry
        subject, room = parse_block_title(title)
        subject, kind = split_course_type(subject)
        activities.append({
            "start": start_str,
            "end": end_str,
            "subject": subject,
            "kind": kind,
            "kind_label": COURSE_TYPE_LABELS.get(kind, ""),
            "room": room,
        })
    busy = sum(
        _hhmm_to_minutes(a["end"]) - _hhmm_to_minutes(a["start"]) for a in activities
    )
    timeline = build_day_timeline(schedule, target_date)
    week_num = academic_week_number(academic_start, target_date)
    return {
        "date": target_date.isoformat(),
        "day_name": day_name,
        "day_label": DAY_DISPLAY[day_name],
        "week_num": week_num,
        "parity": "odd" if is_odd_week(academic_start, target_date) else "even",
        "in_semester": target_date >= academic_start,
        "activities": activities,
        "busy_minutes": busy,
        "segments": busy_segments(entries),
        "free_windows": free_windows(timeline),
    }


def prepare_blocks_for_ui(
    timeline: list[dict],
    current_block: Optional[dict],
) -> list[dict]:
    """Transform raw timeline blocks into template-ready dicts.

    Each returned dict has keys:
        ``type``, ``title``, ``subject``, ``kind``, ``kind_label``, ``room``,
        ``start``, ``end``, ``start_iso``, ``end_iso``, ``duration``,
        ``is_current``.
    """
    blocks: list[dict] = []
    for blk in timeline:
        full_title = blk.get("title", "")
        subject, room = parse_block_title(full_title)
        subject, kind = split_course_type(subject)
        minutes = int((blk["end_dt"] - blk["start_dt"]).total_seconds() // 60)
        # 23:59 is the end-of-day sentinel; count it as a full hour.
        if blk["end_dt"].time() == time(23, 59):
            minutes += 1
        ui = {
            "type": blk["type"],
            "title": full_title,
            "subject": subject,
            "kind": kind,
            "kind_label": COURSE_TYPE_LABELS.get(kind, ""),
            "room": room,
            "start": blk["start_dt"].strftime("%H:%M"),
            "end": blk["end_dt"].strftime("%H:%M"),
            "start_iso": blk["start_dt"].isoformat(),
            "end_iso": blk["end_dt"].isoformat(),
            "duration": format_duration(minutes),
            "is_current": blk is current_block,
        }
        if blk["type"] == "break":
            # Suggested slot for "schedule something here": up to 2h inside
            # the gap, clamped to a sensible 07:00–22:00 daytime window.
            s = blk["start_dt"].hour * 60 + blk["start_dt"].minute
            e = blk["end_dt"].hour * 60 + blk["end_dt"].minute
            s = max(s, 7 * 60)
            e = min(e, 22 * 60)
            if e - s >= 30:
                ui["suggest_start"] = _minutes_to_hhmm(s)
                ui["suggest_end"] = _minutes_to_hhmm(min(e, s + 120))
        blocks.append(ui)
    return blocks
