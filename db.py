"""
Database layer for IamBusy — SQLite-backed storage.

Holds the accounts (users, device sessions, invite codes, rate-limit log)
and one schedule per account.  Every activity query is scoped by
``user_id`` — there is no function that reads or writes another user's
activities.
"""

import os
import sqlite3
import time
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.db")


def _now() -> int:
    return int(time.time())


def _get_conn() -> sqlite3.Connection:
    """Return a new connection with row-factory enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create the tables if they don't exist and lock down the file mode."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS invite_codes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT    NOT NULL UNIQUE,
            note        TEXT    NOT NULL DEFAULT '',
            max_uses    INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
            uses        INTEGER NOT NULL DEFAULT 0,
            expires_at  INTEGER,
            revoked     INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            username       TEXT    NOT NULL UNIQUE,
            display_name   TEXT    NOT NULL,
            password_hash  TEXT    NOT NULL,
            is_admin       INTEGER NOT NULL DEFAULT 0,
            is_active      INTEGER NOT NULL DEFAULT 1,
            week1_start    TEXT    NOT NULL,
            created_at     INTEGER NOT NULL,
            last_login_at  INTEGER,
            invite_id      INTEGER REFERENCES invite_codes(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  TEXT    NOT NULL UNIQUE,
            persistent  INTEGER NOT NULL DEFAULT 1,
            created_at  INTEGER NOT NULL,
            last_seen   INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            user_agent  TEXT    NOT NULL DEFAULT '',
            ip          TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

        CREATE TABLE IF NOT EXISTS auth_attempts (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            key  TEXT    NOT NULL,
            ts   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attempts_key ON auth_attempts(key, ts);

        CREATE TABLE IF NOT EXISTS activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title       TEXT    NOT NULL,
            day         TEXT    NOT NULL,
            start_time  TEXT    NOT NULL,
            end_time    TEXT    NOT NULL,
            week_type   TEXT    NOT NULL DEFAULT 'both'
                        CHECK (week_type IN ('odd', 'even', 'both'))
        );
        CREATE INDEX IF NOT EXISTS idx_activities_user ON activities(user_id, day);
    """)
    conn.commit()
    conn.close()
    try:
        os.chmod(DB_PATH, 0o600)  # password hashes live here
    except OSError:
        pass


# ═══════════════════════════════════════════════════════ Users ══

def create_user(
    username: str,
    display_name: str,
    password_hash: str,
    week1_start: str,
    is_admin: bool = False,
    invite_code: Optional[str] = None,
) -> Optional[int]:
    """Insert a user and return its id.

    With *invite_code*, the code is consumed in the same transaction; the
    function returns ``None`` (and creates nothing) if the code is unknown,
    revoked, expired or used up.  Raises ``sqlite3.IntegrityError`` if the
    username is taken.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        invite_id = None
        if invite_code is not None:
            cur = conn.execute(
                "UPDATE invite_codes SET uses = uses + 1 "
                "WHERE code = ? AND revoked = 0 AND uses < max_uses "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (invite_code, _now()),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            invite_id = conn.execute(
                "SELECT id FROM invite_codes WHERE code = ?", (invite_code,)
            ).fetchone()["id"]
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, is_admin, "
            "week1_start, created_at, invite_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, display_name, password_hash, int(is_admin), week1_start,
             _now(), invite_id),
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    """Every user with session / activity counts (for the admin panel)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT u.id, u.username, u.display_name, u.is_admin, u.is_active, "
        "       u.created_at, u.last_login_at, "
        "       (SELECT COUNT(*) FROM sessions s "
        "         WHERE s.user_id = u.id AND s.expires_at > ?) AS session_count, "
        "       (SELECT COUNT(*) FROM activities a WHERE a.user_id = u.id) AS activity_count, "
        "       (SELECT note FROM invite_codes i WHERE i.id = u.invite_id) AS invite_note "
        "FROM users u ORDER BY u.is_admin DESC, u.created_at",
        (_now(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user(user_id: int, **fields) -> None:
    """Update whitelisted columns of a user."""
    allowed = {"display_name", "password_hash", "is_active", "week1_start", "last_login_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Cannot update {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn = _get_conn()
    conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id))
    conn.commit()
    conn.close()


def delete_user(user_id: int) -> bool:
    """Delete a user; sessions and activities go with it (ON DELETE CASCADE)."""
    conn = _get_conn()
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ═════════════════════════════════════════════════════ Sessions ══

def create_session(
    user_id: int, token_hash: str, expires_at: int, persistent: bool,
    user_agent: str, ip: str,
) -> int:
    now = _now()
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    cur = conn.execute(
        "INSERT INTO sessions (user_id, token_hash, persistent, created_at, last_seen, "
        "expires_at, user_agent, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, token_hash, int(persistent), now, now, expires_at, user_agent, ip),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def get_session(token_hash: str) -> Optional[dict]:
    """Return the live session for *token_hash* joined with its user."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT s.id AS session_id, s.persistent, s.last_seen, s.expires_at, u.* "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ?",
        (token_hash, _now()),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def touch_session(session_id: int, expires_at: int, ip: str) -> None:
    conn = _get_conn()
    conn.execute(
        "UPDATE sessions SET last_seen = ?, expires_at = ?, ip = ? WHERE id = ?",
        (_now(), expires_at, ip, session_id),
    )
    conn.commit()
    conn.close()


def list_sessions(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, persistent, created_at, last_seen, expires_at, user_agent, ip "
        "FROM sessions WHERE user_id = ? AND expires_at > ? ORDER BY last_seen DESC",
        (user_id, _now()),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_session(session_id: int, user_id: int) -> bool:
    """Delete one session — only if it belongs to *user_id*."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_user_sessions(user_id: int, except_session_id: Optional[int] = None) -> int:
    conn = _get_conn()
    if except_session_id is None:
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    else:
        cur = conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND id != ?",
            (user_id, except_session_id),
        )
    conn.commit()
    conn.close()
    return cur.rowcount


# ═════════════════════════════════════════════════ Invite codes ══

def create_invite(code: str, note: str, max_uses: int, expires_at: Optional[int]) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO invite_codes (code, note, max_uses, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, note, max_uses, expires_at, _now()),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_invites() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    now = _now()
    invites = []
    for r in rows:
        inv = dict(r)
        if inv["revoked"]:
            inv["status"] = "revoked"
        elif inv["uses"] >= inv["max_uses"]:
            inv["status"] = "used"
        elif inv["expires_at"] is not None and inv["expires_at"] <= now:
            inv["status"] = "expired"
        else:
            inv["status"] = "active"
        invites.append(inv)
    return invites


def invite_is_usable(code: str) -> bool:
    """True if *code* could be used to register right now."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM invite_codes WHERE code = ? AND revoked = 0 AND uses < max_uses "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (code, _now()),
    ).fetchone()
    conn.close()
    return row is not None


def revoke_invite(invite_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("UPDATE invite_codes SET revoked = 1 WHERE id = ?", (invite_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_invite(invite_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM invite_codes WHERE id = ?", (invite_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ═══════════════════════════════════════════════ Rate limiting ══

def record_attempt(*keys: str) -> None:
    now = _now()
    conn = _get_conn()
    conn.execute("DELETE FROM auth_attempts WHERE ts < ?", (now - 86400,))
    conn.executemany(
        "INSERT INTO auth_attempts (key, ts) VALUES (?, ?)", [(k, now) for k in keys]
    )
    conn.commit()
    conn.close()


def count_attempts(key: str, window_seconds: int) -> int:
    conn = _get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM auth_attempts WHERE key = ? AND ts > ?",
        (key, _now() - window_seconds),
    ).fetchone()[0]
    conn.close()
    return count


def clear_attempts(*keys: str) -> None:
    conn = _get_conn()
    conn.executemany("DELETE FROM auth_attempts WHERE key = ?", [(k,) for k in keys])
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════ Activities ══

def get_schedule(user_id: int, week_type: str) -> dict[str, list[tuple[str, str, str]]]:
    """Return the user's schedule as ``{day: [(title, start, end), …]}``.

    *week_type* should be ``'odd'`` or ``'even'``. Activities stored as
    ``'both'`` are included in either result.
    """
    from schedule_engine import DAYS

    conn = _get_conn()
    rows = conn.execute(
        "SELECT title, day, start_time, end_time FROM activities "
        "WHERE user_id = ? AND (week_type = ? OR week_type = 'both') "
        "ORDER BY start_time",
        (user_id, week_type),
    ).fetchall()
    conn.close()

    schedule: dict[str, list[tuple[str, str, str]]] = {d: [] for d in DAYS}
    for r in rows:
        if r["day"] in schedule:
            schedule[r["day"]].append((r["title"], r["start_time"], r["end_time"]))
    return schedule


def get_all_activities(user_id: int) -> list[dict]:
    """Return every activity of the user as a plain dict (for the JSON API)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, title, day, start_time, end_time, week_type "
        "FROM activities WHERE user_id = ? ORDER BY day, start_time",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_activity(user_id: int, activity_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, title, day, start_time, end_time, week_type "
        "FROM activities WHERE id = ? AND user_id = ?",
        (activity_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def add_activity(
    user_id: int, title: str, day: str, start_time: str, end_time: str,
    week_type: str = "both",
) -> int:
    """Insert a new activity and return its id."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO activities (user_id, title, day, start_time, end_time, week_type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, title, day, start_time, end_time, week_type),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def count_activities(user_id: int) -> int:
    conn = _get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM activities WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return count


def delete_activity(user_id: int, activity_id: int) -> bool:
    """Delete an activity by id. Returns True if a row was removed."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM activities WHERE id = ? AND user_id = ?", (activity_id, user_id)
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def update_activity(
    user_id: int, activity_id: int, title: str, day: str, start_time: str,
    end_time: str, week_type: str,
) -> bool:
    """Replace the fields of an existing activity. Returns True if a row changed."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE activities SET title=?, day=?, start_time=?, end_time=?, week_type=? "
        "WHERE id=? AND user_id=?",
        (title, day, start_time, end_time, week_type, activity_id, user_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def find_conflicts(
    user_id: int,
    day: str,
    start_time: str,
    end_time: str,
    week_type: str,
    exclude_id: Optional[int] = None,
) -> list[dict]:
    """Find the user's activities that overlap the given window on the same day.

    Two intervals overlap when ``start_a < end_b AND start_b < end_a``.
    ``week_type='both'`` conflicts with any week type.
    """
    conn = _get_conn()
    query = """
        SELECT id, title, day, start_time, end_time, week_type
        FROM activities
        WHERE user_id = ?
          AND day = ?
          AND start_time < ?
          AND end_time > ?
          AND (week_type = ? OR week_type = 'both' OR ? = 'both')
    """
    params: list = [user_id, day, end_time, start_time, week_type, week_type]

    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]
