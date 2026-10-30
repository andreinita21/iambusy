"""
Database layer for IamBusy — SQLite-backed storage.

Holds the accounts (users, device sessions, invite codes, rate-limit log)
and one schedule per account.  Every activity query is scoped by
``user_id`` — there is no function that reads or writes another user's
activities.  The social side (friends, groups, visibility rules, shared
activities, notifications, share links) lives in :mod:`db_social`.

Schema changes are additive only (new tables, new nullable columns), so an
older copy of the app keeps working against an upgraded database.
"""

import os
import re
import sqlite3
import time
from typing import Optional

DB_PATH = os.environ.get("IAMBUSY_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "schedule.db")

# Palette keys map to CSS tokens (``var(--blue)`` …).  Red is reserved for
# "busy" blocks so a category can never be mistaken for one.
CATEGORY_COLORS = ("blue", "orange", "purple", "cyan", "pink", "yellow", "lime")
DEFAULT_CATEGORIES = (
    ("Curs", "blue"), ("Seminar", "orange"), ("Laborator", "purple"),
    ("Personal", "pink"), ("Altele", "cyan"),
)
FALLBACK_CATEGORY = "Altele"
_KIND_CATEGORY = {"C": "Curs", "S": "Seminar", "L": "Laborator"}
_KIND_RE = re.compile(r"\(([CSL])\)")

ACTIVITY_COLS = ("id, user_id, title, day, start_time, end_time, week_type, "
                 "category_id, date, valid_from, valid_until")


def _now() -> int:
    return int(time.time())


# Kinds of notification email (users.email_notify lists the ones a user wants).
EMAIL_KIND_LABELS = {
    "friends": "cereri de prietenie",
    "invites": "invitații la activități",
    "answers": "răspunsuri la cererile și invitațiile tale",
    "changes": "activități modificate sau anulate",
}
DEFAULT_EMAIL_NOTIFY = ",".join(EMAIL_KIND_LABELS)


def _get_conn() -> sqlite3.Connection:
    """Return a new connection with row-factory enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Deleted rows (a deleted account, a removed activity) are overwritten on
    # disk instead of lingering in free pages.
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def init_db() -> None:
    """Create the tables if they don't exist, migrate, lock down the file mode."""
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

        -- ── Categories (one per activity, defined per user) ────────────
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            color       TEXT    NOT NULL DEFAULT 'cyan',
            position    INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL,
            UNIQUE (user_id, name)
        );

        -- ── Friends ────────────────────────────────────────────────────
        -- One row per pair (user_lo < user_hi); 'pending' until accepted.
        CREATE TABLE IF NOT EXISTS friendships (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            addressee_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_lo       INTEGER NOT NULL,
            user_hi       INTEGER NOT NULL,
            status        TEXT    NOT NULL CHECK (status IN ('pending', 'accepted')),
            created_at    INTEGER NOT NULL,
            accepted_at   INTEGER,
            UNIQUE (user_lo, user_hi)
        );
        CREATE INDEX IF NOT EXISTS idx_friend_req ON friendships(requester_id);
        CREATE INDEX IF NOT EXISTS idx_friend_addr ON friendships(addressee_id);

        CREATE TABLE IF NOT EXISTS blocks (
            blocker_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            blocked_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  INTEGER NOT NULL,
            PRIMARY KEY (blocker_id, blocked_id)
        );

        -- Groups are private to their owner.
        CREATE TABLE IF NOT EXISTS friend_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            color       TEXT    NOT NULL DEFAULT 'cyan',
            created_at  INTEGER NOT NULL,
            UNIQUE (owner_id, name)
        );

        CREATE TABLE IF NOT EXISTS friend_group_members (
            group_id   INTEGER NOT NULL REFERENCES friend_groups(id) ON DELETE CASCADE,
            member_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            PRIMARY KEY (group_id, member_id)
        );

        -- ── Visibility rules ───────────────────────────────────────────
        -- subject_type 'all' (subject_id 0), 'group' or 'friend'.
        CREATE TABLE IF NOT EXISTS category_rules (
            category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            subject_type  TEXT    NOT NULL CHECK (subject_type IN ('all', 'group', 'friend')),
            subject_id    INTEGER NOT NULL DEFAULT 0,
            level         TEXT    NOT NULL CHECK (level IN ('full', 'busy', 'hidden')),
            PRIMARY KEY (category_id, subject_type, subject_id)
        );

        -- user_id = whose calendar the rule applies to (the owner, or a
        -- participant who accepted the activity into their own calendar).
        CREATE TABLE IF NOT EXISTS activity_rules (
            activity_id   INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subject_type  TEXT    NOT NULL CHECK (subject_type IN ('all', 'group', 'friend')),
            subject_id    INTEGER NOT NULL DEFAULT 0,
            level         TEXT    NOT NULL CHECK (level IN ('full', 'busy', 'hidden')),
            PRIMARY KEY (activity_id, user_id, subject_type, subject_id)
        );

        -- ── Shared activities ──────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS activity_participants (
            activity_id   INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
            user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status        TEXT    NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'accepted', 'declined')),
            category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            invited_at    INTEGER NOT NULL,
            responded_at  INTEGER,
            PRIMARY KEY (activity_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_participants_user ON activity_participants(user_id);

        CREATE TABLE IF NOT EXISTS notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind         TEXT    NOT NULL,
            actor_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
            activity_id  INTEGER REFERENCES activities(id) ON DELETE SET NULL,
            text         TEXT    NOT NULL DEFAULT '',
            created_at   INTEGER NOT NULL,
            read_at      INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at);

        -- ── Links ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS friend_links (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code        TEXT    NOT NULL UNIQUE,
            note        TEXT    NOT NULL DEFAULT '',
            max_uses    INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
            uses        INTEGER NOT NULL DEFAULT 0,
            expires_at  INTEGER,
            revoked     INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS share_links (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token           TEXT    NOT NULL UNIQUE,
            label           TEXT    NOT NULL DEFAULT '',
            preset          TEXT    NOT NULL DEFAULT 'busy'
                            CHECK (preset IN ('busy', 'full', 'friends')),
            window_type     TEXT    NOT NULL DEFAULT 'unlimited'
                            CHECK (window_type IN ('unlimited', 'range', 'rolling', 'days')),
            range_from      TEXT,
            range_to        TEXT,
            rolling_days    INTEGER,
            days_list       TEXT    NOT NULL DEFAULT '',
            expires_at      INTEGER,
            pin_hash        TEXT,
            show_status     INTEGER NOT NULL DEFAULT 1,
            views           INTEGER NOT NULL DEFAULT 0,
            last_viewed_at  INTEGER,
            revoked         INTEGER NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS share_link_rules (
            link_id      INTEGER NOT NULL REFERENCES share_links(id) ON DELETE CASCADE,
            category_id  INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            level        TEXT    NOT NULL CHECK (level IN ('full', 'busy', 'hidden')),
            PRIMARY KEY (link_id, category_id)
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        );
    """)
    conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")  # serialise the workers' migrations
        _migrate(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    try:
        os.chmod(DB_PATH, 0o600)  # password hashes live here
    except OSError:
        pass


def _columns(conn, table: str) -> set:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn) -> None:
    """Additive, idempotent upgrades of databases created by older versions."""
    cols = _columns(conn, "activities")
    for name, decl in (
        ("category_id", "INTEGER REFERENCES categories(id) ON DELETE SET NULL"),
        ("date", "TEXT"),          # set → one-off event on that ISO date
        ("valid_from", "TEXT"),    # optional range of a weekly activity
        ("valid_until", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {name} {decl}")
    user_cols = _columns(conn, "users")
    if "invited_by" not in user_cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
    if "email" not in user_cols:
        # Only ever set after the owner clicked a confirmation link.
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "email_notify" not in user_cols:
        # Which notification emails the user wants (see emails.KINDS).
        conn.execute("ALTER TABLE users ADD COLUMN email_notify TEXT NOT NULL "
                     f"DEFAULT '{DEFAULT_EMAIL_NOTIFY}'")
    if "theme" not in user_cols:
        # "" (default look), "preset:<key>" or "custom:<user_themes.id>" — see themes.py.
        conn.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) "
                 "WHERE email IS NOT NULL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_themes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT    NOT NULL,
            colors      TEXT    NOT NULL,
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_themes_user ON user_themes(user_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose     TEXT    NOT NULL CHECK (purpose IN ('verify', 'reset')),
            token_hash  TEXT    NOT NULL UNIQUE,
            email       TEXT    NOT NULL,
            created_at  INTEGER NOT NULL,
            expires_at  INTEGER NOT NULL,
            used_at     INTEGER
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_tokens_user ON email_tokens(user_id, purpose)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(user_id, date)")

    for row in conn.execute(
        "SELECT id FROM users WHERE id NOT IN (SELECT DISTINCT user_id FROM categories)"
    ).fetchall():
        seed_categories(conn, row["id"])
    _assign_missing_categories(conn)


def seed_categories(conn, user_id: int) -> None:
    now = _now()
    conn.executemany(
        "INSERT OR IGNORE INTO categories (user_id, name, color, position, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(user_id, name, color, i, now) for i, (name, color) in enumerate(DEFAULT_CATEGORIES)],
    )


def guess_category_name(title: str) -> str:
    """``"OOP (L) | C1"`` → ``"Laborator"``; anything else → the fallback."""
    m = _KIND_RE.search(title or "")
    return _KIND_CATEGORY[m.group(1)] if m else FALLBACK_CATEGORY


def _assign_missing_categories(conn) -> None:
    """Give every activity without a (valid, own) category one, by its title."""
    rows = conn.execute(
        "SELECT a.id, a.user_id, a.title FROM activities a "
        "LEFT JOIN categories c ON c.id = a.category_id AND c.user_id = a.user_id "
        "WHERE c.id IS NULL"
    ).fetchall()
    cache: dict = {}
    for r in rows:
        if r["user_id"] not in cache:
            cache[r["user_id"]] = {
                c["name"]: c["id"] for c in conn.execute(
                    "SELECT id, name FROM categories WHERE user_id = ? ORDER BY position, id",
                    (r["user_id"],))
            }
        cats = cache[r["user_id"]]
        if not cats:
            continue
        target = cats.get(guess_category_name(r["title"])) or cats.get(FALLBACK_CATEGORY) \
            or next(iter(cats.values()))
        conn.execute("UPDATE activities SET category_id = ? WHERE id = ?", (target, r["id"]))


# ═══════════════════════════════════════════════════════ Users ══

def create_user(
    username: str,
    display_name: str,
    password_hash: str,
    week1_start: str,
    is_admin: bool = False,
    invite_code: Optional[str] = None,
    friend_code: Optional[str] = None,
) -> Optional[int]:
    """Insert a user and return its id.

    With *invite_code* (an admin invite) or *friend_code* (a user's friend
    link), the code is consumed in the same transaction; the function
    returns ``None`` (and creates nothing) if the code is unknown, revoked,
    expired or used up.  A friend code also makes the new account friends
    with the link's owner.  Raises ``sqlite3.IntegrityError`` if the
    username is taken.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        invite_id = None
        invited_by = None
        if invite_code is not None:
            cur = conn.execute(
                "UPDATE invite_codes SET uses = uses + 1 "
                "WHERE code = ? AND revoked = 0 AND uses < max_uses "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (invite_code, now),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            invite_id = conn.execute(
                "SELECT id FROM invite_codes WHERE code = ?", (invite_code,)
            ).fetchone()["id"]
        elif friend_code is not None:
            cur = conn.execute(
                "UPDATE friend_links SET uses = uses + 1 "
                "WHERE code = ? AND revoked = 0 AND uses < max_uses "
                "AND (expires_at IS NULL OR expires_at > ?) "
                "AND user_id IN (SELECT id FROM users WHERE is_active = 1)",
                (friend_code, now),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            invited_by = conn.execute(
                "SELECT user_id FROM friend_links WHERE code = ?", (friend_code,)
            ).fetchone()["user_id"]
        cur = conn.execute(
            "INSERT INTO users (username, display_name, password_hash, is_admin, "
            "week1_start, created_at, invite_id, invited_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (username, display_name, password_hash, int(is_admin), week1_start,
             now, invite_id, invited_by),
        )
        user_id = cur.lastrowid
        seed_categories(conn, user_id)
        if invited_by is not None:
            lo, hi = sorted((invited_by, user_id))
            conn.execute(
                "INSERT INTO friendships (requester_id, addressee_id, user_lo, user_hi, status, "
                "created_at, accepted_at) VALUES (?, ?, ?, ?, 'accepted', ?, ?)",
                (invited_by, user_id, lo, hi, now, now),
            )
            conn.execute(
                "INSERT INTO notifications (user_id, kind, actor_id, created_at) "
                "VALUES (?, 'friend_joined', ?, ?)",
                (invited_by, user_id, now),
            )
        conn.commit()
        return user_id
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
        "SELECT u.id, u.username, u.display_name, u.email, u.is_admin, u.is_active, "
        "       u.created_at, u.last_login_at, "
        "       (SELECT COUNT(*) FROM sessions s "
        "         WHERE s.user_id = u.id AND s.expires_at > ?) AS session_count, "
        "       (SELECT COUNT(*) FROM activities a WHERE a.user_id = u.id) AS activity_count, "
        "       (SELECT note FROM invite_codes i WHERE i.id = u.invite_id) AS invite_note, "
        "       (SELECT username FROM users p WHERE p.id = u.invited_by) AS invited_by_username "
        "FROM users u ORDER BY u.is_admin DESC, u.created_at",
        (_now(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user(user_id: int, **fields) -> None:
    """Update whitelisted columns of a user."""
    allowed = {"display_name", "password_hash", "is_active", "week1_start", "last_login_at",
               "email", "email_notify", "theme"}
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
    """Delete a user; everything they own goes with it (ON DELETE CASCADE)."""
    conn = _get_conn()
    # Rules naming this user as a friend have no foreign key to cascade on.
    conn.execute("DELETE FROM category_rules WHERE subject_type = 'friend' AND subject_id = ?",
                 (user_id,))
    conn.execute("DELETE FROM activity_rules WHERE subject_type = 'friend' AND subject_id = ?",
                 (user_id,))
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


def _with_status(inv: dict, now: int) -> dict:
    if inv["revoked"]:
        inv["status"] = "revoked"
    elif inv["uses"] >= inv["max_uses"]:
        inv["status"] = "used"
    elif inv["expires_at"] is not None and inv["expires_at"] <= now:
        inv["status"] = "expired"
    else:
        inv["status"] = "active"
    return inv


def list_invites() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    now = _now()
    return [_with_status(dict(r), now) for r in rows]


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


def purge_expired() -> None:
    """Drop rate-limit records older than a day and expired sessions (run daily)."""
    now = _now()
    conn = _get_conn()
    conn.execute("DELETE FROM email_tokens WHERE expires_at <= ?", (now - 86400,))
    conn.execute("DELETE FROM auth_attempts WHERE ts < ?", (now - 86400,))
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
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


# ═══════════════════════════════════════════════════ Settings ══

def get_setting(key: str, default: str = "") -> str:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════ Categories ══

def list_categories(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT c.id, c.name, c.color, c.position, "
        "       (SELECT COUNT(*) FROM activities a WHERE a.category_id = c.id) AS activity_count "
        "FROM categories c WHERE c.user_id = ? ORDER BY c.position, c.id",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_category(user_id: int, category_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id, name, color, position FROM categories WHERE id = ? AND user_id = ?",
        (category_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_category(user_id: int, name: str, color: str) -> int:
    """Raises ``sqlite3.IntegrityError`` if the name is taken."""
    conn = _get_conn()
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM categories WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO categories (user_id, name, color, position, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, color, pos, _now()),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def update_category(user_id: int, category_id: int, name: str, color: str) -> bool:
    """Raises ``sqlite3.IntegrityError`` if the new name is taken."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE categories SET name = ?, color = ? WHERE id = ? AND user_id = ?",
        (name, color, category_id, user_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_category(user_id: int, category_id: int, move_to: int) -> bool:
    """Delete a category, moving its activities (and accepted invites) to *move_to*."""
    if category_id == move_to:
        return False
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        owned = conn.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ? AND id IN (?, ?)",
            (user_id, category_id, move_to),
        ).fetchone()[0]
        if owned != 2:
            conn.rollback()
            return False
        conn.execute("UPDATE activities SET category_id = ? WHERE category_id = ? AND user_id = ?",
                     (move_to, category_id, user_id))
        conn.execute(
            "UPDATE activity_participants SET category_id = ? WHERE category_id = ? AND user_id = ?",
            (move_to, category_id, user_id))
        conn.execute("DELETE FROM categories WHERE id = ? AND user_id = ?", (category_id, user_id))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fallback_category_id(user_id: int) -> Optional[int]:
    """The category new or unclassifiable activities land in."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM categories WHERE user_id = ? "
        "ORDER BY (name = ?) DESC, position, id LIMIT 1",
        (user_id, FALLBACK_CATEGORY),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


# ══════════════════════════════════════════════════ Activities ══

def get_all_activities(user_id: int) -> list[dict]:
    """Return every activity the user owns as a plain dict (for the JSON API)."""
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT {ACTIVITY_COLS} FROM activities WHERE user_id = ? "
        "ORDER BY date IS NOT NULL, date, day, start_time",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_activity(user_id: int, activity_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        f"SELECT {ACTIVITY_COLS} FROM activities WHERE id = ? AND user_id = ?",
        (activity_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _dated_fields(date: Optional[str], day: str, week_type: str) -> tuple[str, str]:
    """A one-off event's weekday always follows its date."""
    if not date:
        return day, week_type
    from datetime import date as _date
    from schedule_engine import DAYS
    return DAYS[_date.fromisoformat(date).weekday()], "both"


def add_activity(
    user_id: int, title: str, day: str, start_time: str, end_time: str,
    week_type: str = "both", *, category_id: Optional[int] = None,
    date: Optional[str] = None, valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> int:
    """Insert a new activity and return its id.

    Without *category_id* the category is guessed from the title, as the
    migration does for old activities.
    """
    day, week_type = _dated_fields(date, day, week_type)
    conn = _get_conn()
    if category_id is None:
        cats = {c["name"]: c["id"] for c in conn.execute(
            "SELECT id, name FROM categories WHERE user_id = ? ORDER BY position, id", (user_id,))}
        category_id = cats.get(guess_category_name(title)) or cats.get(FALLBACK_CATEGORY) \
            or next(iter(cats.values()), None)
    cur = conn.execute(
        "INSERT INTO activities (user_id, title, day, start_time, end_time, week_type, "
        "category_id, date, valid_from, valid_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, day, start_time, end_time, week_type, category_id, date,
         valid_from, valid_until),
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
    end_time: str, week_type: str, *, category_id: Optional[int] = None,
    date: Optional[str] = None, valid_from: Optional[str] = None,
    valid_until: Optional[str] = None,
) -> bool:
    """Replace the fields of an existing activity. Returns True if a row changed."""
    day, week_type = _dated_fields(date, day, week_type)
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE activities SET title=?, day=?, start_time=?, end_time=?, week_type=?, "
        "category_id=COALESCE(?, category_id), date=?, valid_from=?, valid_until=? "
        "WHERE id=? AND user_id=?",
        (title, day, start_time, end_time, week_type, category_id, date, valid_from,
         valid_until, activity_id, user_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ═══════════════════════════════════════════════ Email tokens ══

def get_user_by_email(email: str) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_email_token(user_id: int, purpose: str, token_hash: str, email: str,
                       lifetime: int) -> None:
    """A new link replaces any older unused one of the same kind."""
    now = _now()
    conn = _get_conn()
    conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ? AND used_at IS NULL",
                 (user_id, purpose))
    conn.execute(
        "INSERT INTO email_tokens (user_id, purpose, token_hash, email, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (user_id, purpose, token_hash, email, now, now + lifetime))
    conn.commit()
    conn.close()


def get_email_token(purpose: str, token_hash: str) -> Optional[dict]:
    """The token and its user, if it is unused, unexpired and the account active."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT t.*, u.username, u.display_name, u.email AS current_email FROM email_tokens t "
        "JOIN users u ON u.id = t.user_id "
        "WHERE t.purpose = ? AND t.token_hash = ? AND t.used_at IS NULL AND t.expires_at > ? "
        "AND u.is_active = 1", (purpose, token_hash, _now())).fetchone()
    conn.close()
    return dict(row) if row else None


def use_email_token(token_id: int) -> bool:
    """Mark a token used; ``False`` if someone else got there first."""
    conn = _get_conn()
    cur = conn.execute("UPDATE email_tokens SET used_at = ? WHERE id = ? AND used_at IS NULL",
                       (_now(), token_id))
    conn.commit()
    conn.close()
    return cur.rowcount == 1


def drop_email_tokens(user_id: int, purpose: Optional[str] = None) -> None:
    conn = _get_conn()
    if purpose:
        conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?",
                     (user_id, purpose))
    else:
        conn.execute("DELETE FROM email_tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def set_verified_email(user_id: int, email: str) -> bool:
    """Attach a confirmed address; ``False`` if another account already has it."""
    conn = _get_conn()
    try:
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def pending_email(user_id: int) -> str:
    """An address still waiting for its confirmation click, if any."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT email FROM email_tokens WHERE user_id = ? AND purpose = 'verify' "
        "AND used_at IS NULL AND expires_at > ? ORDER BY id DESC LIMIT 1",
        (user_id, _now())).fetchone()
    conn.close()
    return row["email"] if row else ""


# ═════════════════════════════════════════════════ Colour themes ══

def list_user_themes(user_id: int) -> list:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM user_themes WHERE user_id = ? ORDER BY id",
                        (user_id,)).fetchall()
    conn.close()
    return rows


def get_user_theme(user_id: int, theme_id: int):
    conn = _get_conn()
    row = conn.execute("SELECT * FROM user_themes WHERE id = ? AND user_id = ?",
                       (theme_id, user_id)).fetchone()
    conn.close()
    return row


def save_user_theme(user_id: int, name: str, colors: str, theme_id: int = None):
    """Create a theme (or update *theme_id*, if it is the user's); returns its id or ``None``."""
    now = int(time.time())
    conn = _get_conn()
    if theme_id is None:
        cur = conn.execute("INSERT INTO user_themes (user_id, name, colors, created_at, updated_at) "
                           "VALUES (?, ?, ?, ?, ?)", (user_id, name, colors, now, now))
        theme_id = cur.lastrowid
    elif not conn.execute("UPDATE user_themes SET name = ?, colors = ?, updated_at = ? "
                          "WHERE id = ? AND user_id = ?",
                          (name, colors, now, theme_id, user_id)).rowcount:
        theme_id = None
    conn.commit()
    conn.close()
    return theme_id


def delete_user_theme(user_id: int, theme_id: int) -> bool:
    conn = _get_conn()
    deleted = conn.execute("DELETE FROM user_themes WHERE id = ? AND user_id = ?",
                           (theme_id, user_id)).rowcount
    if deleted:
        conn.execute("UPDATE users SET theme = '' WHERE id = ? AND theme = ?",
                     (user_id, f"custom:{theme_id}"))
    conn.commit()
    conn.close()
    return bool(deleted)
