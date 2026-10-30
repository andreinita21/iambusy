"""
Schedule Engine — Core business logic for IamBusy.

All schedule computation, timeline building, and status derivation
lives here, fully decoupled from Flask and the HTTP layer.

A day is described by a list of *entries* — dicts with at least
``title``, ``start`` and ``end`` (``HH:MM``).  Entries may carry display
data (``color``, ``category``, ``masked`` …) which is passed through to the
UI blocks untouched.
"""

import re
from datetime import datetime, timedelta, time, date
from typing import Callable, Iterable, Optional


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

BUSY_LABEL = "Ocupat"


# ──────────────────────────────────────── schedule validation ──
def parse_time_safe(value: str) -> Optional[time]:
    """Parse an ``HH:MM`` string into a :class:`~datetime.time`, or *None*."""
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (ValueError, TypeError):
        return None


def valid_entry(entry) -> bool:
    """True if *entry* is a dict with a string title and ``start < end``."""
    if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
        return False
    start, end = parse_time_safe(entry.get("start")), parse_time_safe(entry.get("end"))
    return start is not None and end is not None and end > start


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



WEEK_LABELS = {"both": "", "odd": "săpt. impară", "even": "săpt. pară"}


def when_label(act: dict) -> str:
    """``Vineri, 7 noiembrie · 18:00–20:00`` or ``Luni · 10:00–12:00 · săpt. impară``."""
    times = f"{act['start_time']}–{act['end_time']}"
    if act.get("date"):
        d = date.fromisoformat(act["date"])
        return f"{DAY_DISPLAY[DAYS[d.weekday()]]}, {format_date_ro(d, long=True)} {d.year} · {times}"
    parts = [f"În fiecare {DAY_DISPLAY[act['day']].lower()}", times]
    if WEEK_LABELS.get(act.get("week_type")):
        parts.append(WEEK_LABELS[act["week_type"]])
    if act.get("valid_from") or act.get("valid_until"):
        start = format_date_ro(date.fromisoformat(act["valid_from"])) if act.get("valid_from") else "…"
        end = format_date_ro(date.fromisoformat(act["valid_until"])) if act.get("valid_until") else "…"
        parts.append(f"{start} – {end}")
    return " · ".join(parts)

def relative_day_ro(d: date, today: date) -> str:
    """Return ``azi`` / ``mâine`` or ``luni, 28 sep`` relative to *today*."""
    delta = (d - today).days
    if delta == 0:
        return "azi"
    if delta == 1:
        return "mâine"
    return f"{DAY_DISPLAY[DAYS[d.weekday()]].lower()}, {format_date_ro(d)}"


# ──────────────────────────────────────────── recurrence ──
def occurs_on(act: dict, d: date, academic_start: date) -> bool:
    """Does activity row *act* happen on date *d*?

    * One-off events (``date`` set) happen on that date only.
    * Weekly activities happen on their weekday, in weeks of the right
      parity, from the start of the academic year on, and only inside their
      optional ``valid_from`` … ``valid_until`` range.
    """
    iso = d.isoformat()
    if act.get("date"):
        return act["date"] == iso
    if act.get("day") != DAYS[d.weekday()] or d < academic_start:
        return False
    if act.get("valid_from") and iso < act["valid_from"]:
        return False
    if act.get("valid_until") and iso > act["valid_until"]:
        return False
    week_type = act.get("week_type", "both")
    if week_type == "both":
        return True
    return (week_type == "odd") == is_odd_week(academic_start, d)


def _ranges_overlap(a_from, a_to, b_from, b_to) -> bool:
    """Inclusive ISO-date ranges; ``None`` means open-ended."""
    return (a_to is None or b_from is None or b_from <= a_to) and \
           (b_to is None or a_from is None or a_from <= b_to)


def can_coincide(a: dict, b: dict, academic_start: date) -> bool:
    """Could activities *a* and *b* ever fall on the same day?"""
    if a.get("date") and b.get("date"):
        return a["date"] == b["date"]
    if a.get("date") or b.get("date"):
        dated, weekly = (a, b) if a.get("date") else (b, a)
        try:
            d = date.fromisoformat(dated["date"])
        except ValueError:
            return False
        return occurs_on(weekly, d, academic_start)
    if a.get("day") != b.get("day"):
        return False
    wa, wb = a.get("week_type", "both"), b.get("week_type", "both")
    if not (wa == wb or "both" in (wa, wb)):
        return False
    return _ranges_overlap(a.get("valid_from"), a.get("valid_until"),
                           b.get("valid_from"), b.get("valid_until"))


def find_conflicts(candidate: dict, existing: Iterable[dict], academic_start: date,
                   exclude_id: Optional[int] = None) -> list[dict]:
    """Activities from *existing* that overlap *candidate* in time on some day.

    Two intervals overlap when ``start_a < end_b AND start_b < end_a``.
    """
    return [
        act for act in existing
        if act.get("id") != exclude_id
        and act["start_time"] < candidate["end_time"]
        and candidate["start_time"] < act["end_time"]
        and can_coincide(candidate, act, academic_start)
    ]


# ────────────────────────── next-activity look-ahead ──
def find_next_activity(
    entries_for: Callable[[date], list[dict]],
    from_date: date,
    until: Optional[date] = None,
    days_ahead: int = 14,
) -> Optional[dict]:
    """Find the first activity on a day after *from_date*.

    *entries_for* returns the (already visibility-filtered) entries of a
    date.  The search stops after *days_ahead* days or at *until*.

    Returns
    -------
    ``{"day_name": str, "date": date, "start_time": str}`` or *None*.
    """
    for offset in range(1, days_ahead + 1):
        candidate = from_date + timedelta(days=offset)
        if until is not None and candidate > until:
            break
        starts = [e["start"] for e in entries_for(candidate) if valid_entry(e)]
        if starts:
            return {
                "day_name": DAYS[candidate.weekday()],
                "date": candidate,
                "start_time": min(starts),
            }
    return None


# ─────────────────────────────────────── timeline construction ──
def build_day_timeline(entries: list[dict], target_date: Optional[date] = None) -> list[dict]:
    """Build an ordered list of time-blocks (courses + breaks) for a day.

    Each block is a dict with keys:
        ``type`` ("course" | "break"), ``title`` (str, courses only),
        ``start_dt``, ``end_dt`` (datetime), ``entry`` (courses only).

    Overlapping entries (e.g. an accepted invite on top of a course) are
    kept as separate course blocks; breaks only appear where nothing is
    scheduled.
    """
    if target_date is None:
        target_date = datetime.now().date()

    intervals: list[dict] = []
    for entry in entries:
        if not valid_entry(entry):
            continue
        intervals.append({
            "type": "course",
            "title": entry["title"].strip(),
            "start_dt": datetime.combine(target_date, parse_time_safe(entry["start"])),
            "end_dt": datetime.combine(target_date, parse_time_safe(entry["end"])),
            "entry": entry,
        })

    intervals.sort(key=lambda x: (x["start_dt"], x["end_dt"]))

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
        cursor = max(cursor, iv["end_dt"])

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


def _busy_text(user_name: str, block: dict, end_str: str) -> str:
    entry = block.get("entry") or {}
    if entry.get("masked"):
        return f"{user_name} are ceva programat până la ora {end_str}."
    subject, _room = parse_block_title(block.get("title", ""))
    subject, _kind = split_course_type(subject)
    return f"{user_name} are {subject or 'o activitate'} până la ora {end_str}."


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

    # Normal case: find which block we are in right now.  With overlapping
    # activities the one ending last decides how long we stay busy.
    current_block: Optional[dict] = None
    for blk in timeline:
        if blk["start_dt"] <= now_dt < blk["end_dt"]:
            if current_block is None or (
                    blk["type"] == "course" and blk["end_dt"] > current_block["end_dt"]):
                current_block = blk

    if current_block is None:
        current_block = timeline[0]

    end_str = current_block["end_dt"].strftime("%H:%M")
    minutes = minutes_until(current_block["end_dt"], now_dt)

    if current_block["type"] == "course":
        status_main = _busy_text(user_name, current_block, end_str)
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


def hhmm_to_minutes(value: str) -> int:
    """Convert ``HH:MM`` to minutes since midnight (0 on parse failure)."""
    t = parse_time_safe(value)
    return t.hour * 60 + t.minute if t else 0


def minutes_to_hhmm(total: int) -> str:
    total = max(0, min(total, 23 * 60 + 59))
    return f"{total // 60:02d}:{total % 60:02d}"


def entry_color(entry: dict) -> str:
    """CSS colour key of an entry: ``busy`` for masked ones."""
    if entry.get("masked"):
        return "busy"
    return entry.get("color") or "x"


def busy_segments(
    entries: list[dict],
    window_start_hour: int = 7,
    window_end_hour: int = 22,
) -> list[dict]:
    """Return activity intervals as percentages of a daily hour window.

    Used to draw the tiny "busy bar" under each day in the week strip and
    the month calendar.  Each segment is ``{"left": %, "width": %, "kind"}``
    where *kind* is the colour key.
    """
    w0 = window_start_hour * 60
    w1 = window_end_hour * 60
    span = w1 - w0
    segments: list[dict] = []
    for entry in entries:
        if not valid_entry(entry):
            continue
        s = max(w0, hhmm_to_minutes(entry["start"]))
        e = min(w1, hhmm_to_minutes(entry["end"]))
        if e <= s:
            continue
        segments.append({
            "left": round((s - w0) / span * 100, 2),
            "width": round((e - s) / span * 100, 2),
            "kind": entry_color(entry),
        })
    return segments


def free_windows(timeline: list[dict], min_minutes: int = 30) -> list[dict]:
    """Return the gaps *between* courses that last at least *min_minutes*.

    Leading / trailing free time is excluded — it is implied by the first
    and last course.  Each window is ``{"start", "end", "duration"}``.
    """
    breaks = [b for b in timeline if b["type"] == "break"]
    if not any(b["type"] == "course" for b in timeline):
        return []
    windows: list[dict] = []
    for blk in breaks:
        if blk is timeline[0] or blk is timeline[-1]:
            continue
        gap = int((blk["end_dt"] - blk["start_dt"]).total_seconds() // 60)
        if gap >= min_minutes:
            windows.append({
                "start": blk["start_dt"].strftime("%H:%M"),
                "end": blk["end_dt"].strftime("%H:%M"),
                "duration": format_duration(gap),
            })
    return windows


def describe_entry(entry: dict) -> dict:
    """Title parsing + display fields shared by the day view and the API."""
    if entry.get("masked"):
        return {"subject": BUSY_LABEL, "kind": "", "kind_label": "", "room": "",
                "category": "", "color": "busy", "masked": True}
    subject, room = parse_block_title(entry.get("title", ""))
    subject, kind = split_course_type(subject)
    category = entry.get("category", "")
    kind_label = COURSE_TYPE_LABELS.get(kind, "")
    return {
        "subject": subject,
        "kind": kind,
        "kind_label": "" if kind_label == category else kind_label,
        "room": room,
        "category": category,
        "color": entry_color(entry),
        "masked": False,
    }


def day_summary(
    entries: list[dict],
    target_date: date,
    academic_start: date,
) -> dict:
    """Compact JSON-ready description of one calendar day.

    Used by the month calendar so a user can see, before picking a date,
    how busy that day already is.
    """
    day_name = DAYS[target_date.weekday()]
    activities: list[dict] = []
    for entry in sorted((e for e in entries if valid_entry(e)), key=lambda e: e["start"]):
        info = describe_entry(entry)
        activities.append({
            "start": entry["start"],
            "end": entry["end"],
            "subject": info["subject"],
            "kind": info["color"],
            "kind_label": info["category"] or info["kind_label"],
            "room": info["room"],
            "masked": info["masked"],
            "shared": bool(entry.get("with")),
        })
    busy = sum(
        hhmm_to_minutes(a["end"]) - hhmm_to_minutes(a["start"]) for a in activities
    )
    timeline = build_day_timeline(entries, target_date)
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
        ``category``, ``color``, ``masked``, ``with``, ``start``, ``end``,
        ``start_iso``, ``end_iso``, ``duration``, ``is_current``.
    """
    blocks: list[dict] = []
    for blk in timeline:
        entry = blk.get("entry") or {}
        info = describe_entry(entry) if blk["type"] == "course" else {
            "subject": "", "kind": "", "kind_label": "", "room": "", "category": "",
            "color": "", "masked": False}
        minutes = int((blk["end_dt"] - blk["start_dt"]).total_seconds() // 60)
        # 23:59 is the end-of-day sentinel; count it as a full hour.
        if blk["end_dt"].time() == time(23, 59):
            minutes += 1
        ui = {
            "type": blk["type"],
            "title": "" if info["masked"] else blk.get("title", ""),
            **info,
            "with": entry.get("with", []),
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
                ui["suggest_start"] = minutes_to_hhmm(s)
                ui["suggest_end"] = minutes_to_hhmm(min(e, s + 120))
        blocks.append(ui)
    return blocks


# ─────────────────────────────────────────── interval maths ──
def merge_intervals(intervals: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping / touching ``(start_min, end_min)`` intervals."""
    merged: list[list[int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def free_between(busy: list[tuple[int, int]], window: tuple[int, int],
                 min_minutes: int = 30) -> list[tuple[int, int]]:
    """Gaps of at least *min_minutes* inside *window* not covered by *busy*."""
    w0, w1 = window
    free: list[tuple[int, int]] = []
    cursor = w0
    for s, e in merge_intervals(busy):
        if e <= w0 or s >= w1:
            continue
        if s > cursor:
            free.append((cursor, min(s, w1)))
        cursor = max(cursor, e)
    if cursor < w1:
        free.append((cursor, w1))
    return [(s, e) for s, e in free if e - s >= min_minutes]
