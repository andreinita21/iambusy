"""
Database layer for IamBusy — SQLite-backed schedule storage.

Provides CRUD operations for schedule activities and conflict detection.
"""

import sqlite3
import os
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.db")


def _get_conn() -> sqlite3.Connection:
    """Return a new connection with row-factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create the activities table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            day         TEXT    NOT NULL,
            start_time  TEXT    NOT NULL,
            end_time    TEXT    NOT NULL,
            week_type   TEXT    NOT NULL DEFAULT 'both'
                        CHECK (week_type IN ('odd', 'even', 'both'))
        )
    """)
    conn.commit()
    conn.close()


def is_empty() -> bool:
    """Return True if the activities table has zero rows."""
    conn = _get_conn()
    count = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    conn.close()
    return count == 0


def seed_from_config(schedule_odd: dict, schedule_even: dict) -> None:
    """Populate the DB from the hardcoded config dicts.

    Activities that appear identically in both odd and even weeks are
    stored once with ``week_type='both'``.
    """
    # Build sets for overlap detection
    odd_set: set[tuple[str, str, str, str]] = set()
    even_set: set[tuple[str, str, str, str]] = set()

    for day, entries in schedule_odd.items():
        for title, start, end in entries:
            odd_set.add((title, day, start, end))

    for day, entries in schedule_even.items():
        for title, start, end in entries:
            even_set.add((title, day, start, end))

    both = odd_set & even_set
    only_odd = odd_set - both
    only_even = even_set - both

    conn = _get_conn()
    cur = conn.cursor()

    for title, day, start, end in both:
        cur.execute(
            "INSERT INTO activities (title, day, start_time, end_time, week_type) "
            "VALUES (?, ?, ?, ?, 'both')",
            (title, day, start, end),
        )
    for title, day, start, end in only_odd:
        cur.execute(
            "INSERT INTO activities (title, day, start_time, end_time, week_type) "
            "VALUES (?, ?, ?, ?, 'odd')",
            (title, day, start, end),
        )
    for title, day, start, end in only_even:
        cur.execute(
            "INSERT INTO activities (title, day, start_time, end_time, week_type) "
            "VALUES (?, ?, ?, ?, 'even')",
            (title, day, start, end),
        )

    conn.commit()
    conn.close()


def get_schedule(week_type: str) -> dict[str, list[tuple[str, str, str]]]:
    """Return schedule dict in the same format as SCHEDULE_ODD/SCHEDULE_EVEN.

    *week_type* should be ``'odd'`` or ``'even'``. Activities stored as
    ``'both'`` are included in either result.
    """
    from schedule_engine import DAYS

    conn = _get_conn()
    rows = conn.execute(
        "SELECT title, day, start_time, end_time FROM activities "
        "WHERE week_type = ? OR week_type = 'both' "
        "ORDER BY start_time",
        (week_type,),
    ).fetchall()
    conn.close()

    schedule: dict[str, list[tuple[str, str, str]]] = {d: [] for d in DAYS}
    for r in rows:
        schedule[r["day"]].append((r["title"], r["start_time"], r["end_time"]))
    return schedule


def get_all_activities() -> list[dict]:
    """Return every activity as a plain dict (for the JSON API)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, day, start_time, end_time, week_type "
        "FROM activities ORDER BY day, start_time"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_activity(
    title: str, day: str, start_time: str, end_time: str, week_type: str = "both"
) -> int:
    """Insert a new activity and return its id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO activities (title, day, start_time, end_time, week_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (title, day, start_time, end_time, week_type),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def delete_activity(activity_id: int) -> bool:
    """Delete an activity by id. Returns True if a row was removed."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def update_activity(
    activity_id: int,
    title: Optional[str] = None,
    day: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    week_type: Optional[str] = None,
) -> bool:
    """Update fields of an existing activity. Returns True if a row was changed."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM activities WHERE id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        conn.close()
        return False

    new_title = title if title is not None else row["title"]
    new_day = day if day is not None else row["day"]
    new_start = start_time if start_time is not None else row["start_time"]
    new_end = end_time if end_time is not None else row["end_time"]
    new_wt = week_type if week_type is not None else row["week_type"]

    conn.execute(
        "UPDATE activities SET title=?, day=?, start_time=?, end_time=?, week_type=? "
        "WHERE id=?",
        (new_title, new_day, new_start, new_end, new_wt, activity_id),
    )
    conn.commit()
    conn.close()
    return True


def find_conflicts(
    day: str,
    start_time: str,
    end_time: str,
    week_type: str,
    exclude_id: Optional[int] = None,
) -> list[dict]:
    """Find activities that overlap the given time window on the same day.

    Two intervals overlap when ``start_a < end_b AND start_b < end_a``.
    ``week_type='both'`` conflicts with any week type.
    """
    conn = _get_conn()
    query = """
        SELECT id, title, day, start_time, end_time, week_type
        FROM activities
        WHERE day = ?
          AND start_time < ?
          AND end_time > ?
          AND (week_type = ? OR week_type = 'both' OR ? = 'both')
    """
    params: list = [day, end_time, start_time, week_type, week_type]

    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
