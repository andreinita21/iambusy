"""
Social storage for IamBusy — friends, groups, visibility rules, shared
activities, notifications, friend links and public share links.

Every function takes the acting user's id and only touches rows that user
is allowed to touch; the HTTP layer never builds SQL itself.
"""

import json
import sqlite3
from typing import Iterable, Optional

from db import _get_conn, _now

SUBJECT_TYPES = ("all", "group", "friend")
LEVELS = ("full", "busy", "hidden")

USER_COLS = "u.id, u.username, u.display_name"


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


# ═════════════════════════════════════════════════════ Friends ══

def friendship_between(a: int, b: int) -> Optional[dict]:
    lo, hi = _pair(a, b)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def are_friends(a: int, b: int) -> bool:
    f = friendship_between(a, b)
    return bool(f and f["status"] == "accepted")


def is_blocked(a: int, b: int) -> bool:
    """True if either user blocked the other."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) "
        "OR (blocker_id = ? AND blocked_id = ?)",
        (a, b, b, a),
    ).fetchone()
    conn.close()
    return row is not None


def send_friend_request(from_id: int, to_id: int) -> str:
    """Return ``sent``, ``accepted`` (they had asked us already), ``already``,
    ``pending`` (we asked before) or ``blocked``."""
    if from_id == to_id:
        return "already"
    lo, hi = _pair(from_id, to_id)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) "
            "OR (blocker_id = ? AND blocked_id = ?)", (from_id, to_id, to_id, from_id)
        ).fetchone():
            conn.rollback()
            return "blocked"
        row = conn.execute(
            "SELECT * FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi)
        ).fetchone()
        now = _now()
        if row is None:
            conn.execute(
                "INSERT INTO friendships (requester_id, addressee_id, user_lo, user_hi, status, "
                "created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                (from_id, to_id, lo, hi, now),
            )
            result = "sent"
        elif row["status"] == "accepted":
            result = "already"
        elif row["requester_id"] == from_id:
            result = "pending"
        else:
            conn.execute("UPDATE friendships SET status = 'accepted', accepted_at = ? WHERE id = ?",
                         (now, row["id"]))
            conn.execute(
                "INSERT INTO notifications (user_id, kind, actor_id, created_at) "
                "VALUES (?, 'friend_accepted', ?, ?)", (to_id, from_id, now))
            result = "accepted"
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def accept_friend_request(user_id: int, requester_id: int) -> bool:
    lo, hi = _pair(user_id, requester_id)
    now = _now()
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE friendships SET status = 'accepted', accepted_at = ? "
        "WHERE user_lo = ? AND user_hi = ? AND addressee_id = ? AND status = 'pending'",
        (now, lo, hi, user_id),
    )
    if cur.rowcount:
        conn.execute(
            "INSERT INTO notifications (user_id, kind, actor_id, created_at) "
            "VALUES (?, 'friend_accepted', ?, ?)", (requester_id, user_id, now))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def _cleanup_pair(conn, a: int, b: int) -> None:
    """Remove everything that only makes sense between friends."""
    for owner, other in ((a, b), (b, a)):
        conn.execute(
            "DELETE FROM category_rules WHERE subject_type = 'friend' AND subject_id = ? "
            "AND category_id IN (SELECT id FROM categories WHERE user_id = ?)", (other, owner))
        conn.execute(
            "DELETE FROM activity_rules WHERE subject_type = 'friend' AND subject_id = ? "
            "AND user_id = ?", (other, owner))
        conn.execute(
            "DELETE FROM friend_group_members WHERE member_id = ? "
            "AND group_id IN (SELECT id FROM friend_groups WHERE owner_id = ?)", (other, owner))
        conn.execute(
            "DELETE FROM activity_participants WHERE user_id = ? "
            "AND activity_id IN (SELECT id FROM activities WHERE user_id = ?)", (other, owner))
        conn.execute(
            "DELETE FROM activity_rules WHERE user_id = ? "
            "AND activity_id IN (SELECT id FROM activities WHERE user_id = ?)", (other, owner))


def remove_friendship(user_id: int, other_id: int) -> bool:
    """Unfriend, decline an incoming request or cancel an outgoing one."""
    lo, hi = _pair(user_id, other_id)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi))
        _cleanup_pair(conn, user_id, other_id)
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def block_user(blocker_id: int, blocked_id: int) -> None:
    lo, hi = _pair(blocker_id, blocked_id)
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM friendships WHERE user_lo = ? AND user_hi = ?", (lo, hi))
        _cleanup_pair(conn, blocker_id, blocked_id)
        conn.execute("INSERT OR IGNORE INTO blocks (blocker_id, blocked_id, created_at) "
                     "VALUES (?, ?, ?)", (blocker_id, blocked_id, _now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def unblock_user(blocker_id: int, blocked_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
                       (blocker_id, blocked_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def list_blocked(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT {USER_COLS} FROM blocks b JOIN users u ON u.id = b.blocked_id "
        "WHERE b.blocker_id = ? ORDER BY u.username", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_friends(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        f"SELECT {USER_COLS}, u.week1_start, f.accepted_at FROM friendships f "
        "JOIN users u ON u.id = CASE WHEN f.user_lo = ? THEN f.user_hi ELSE f.user_lo END "
        "WHERE (f.user_lo = ? OR f.user_hi = ?) AND f.status = 'accepted' AND u.is_active = 1 "
        "ORDER BY u.display_name COLLATE NOCASE",
        (user_id, user_id, user_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def friend_ids(user_id: int) -> set[int]:
    return {f["id"] for f in list_friends(user_id)}


def list_requests(user_id: int) -> tuple[list[dict], list[dict]]:
    """(incoming, outgoing) pending requests."""
    conn = _get_conn()
    incoming = conn.execute(
        f"SELECT {USER_COLS}, f.created_at FROM friendships f JOIN users u ON u.id = f.requester_id "
        "WHERE f.addressee_id = ? AND f.status = 'pending' AND u.is_active = 1 "
        "ORDER BY f.created_at DESC", (user_id,)).fetchall()
    outgoing = conn.execute(
        f"SELECT {USER_COLS}, f.created_at FROM friendships f JOIN users u ON u.id = f.addressee_id "
        "WHERE f.requester_id = ? AND f.status = 'pending' ORDER BY f.created_at DESC",
        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in incoming], [dict(r) for r in outgoing]


def count_outgoing_today(user_id: int) -> int:
    conn = _get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM friendships WHERE requester_id = ? AND created_at > ?",
        (user_id, _now() - 86400)).fetchone()[0]
    conn.close()
    return n


# ══════════════════════════════════════════════════════ Groups ══

def list_groups(owner_id: int) -> list[dict]:
    conn = _get_conn()
    groups = [dict(r) for r in conn.execute(
        "SELECT id, name, color FROM friend_groups WHERE owner_id = ? ORDER BY name COLLATE NOCASE",
        (owner_id,))]
    members = conn.execute(
        "SELECT m.group_id, m.member_id FROM friend_group_members m "
        "JOIN friend_groups g ON g.id = m.group_id WHERE g.owner_id = ?", (owner_id,)).fetchall()
    conn.close()
    by_group: dict = {}
    for m in members:
        by_group.setdefault(m["group_id"], []).append(m["member_id"])
    for g in groups:
        g["member_ids"] = by_group.get(g["id"], [])
    return groups


def get_group(owner_id: int, group_id: int) -> Optional[dict]:
    return next((g for g in list_groups(owner_id) if g["id"] == group_id), None)


def create_group(owner_id: int, name: str, color: str) -> int:
    """Raises ``sqlite3.IntegrityError`` if the name is taken."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO friend_groups (owner_id, name, color, created_at) VALUES (?, ?, ?, ?)",
        (owner_id, name, color, _now()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def update_group(owner_id: int, group_id: int, name: str, color: str) -> bool:
    conn = _get_conn()
    cur = conn.execute("UPDATE friend_groups SET name = ?, color = ? WHERE id = ? AND owner_id = ?",
                       (name, color, group_id, owner_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_group(owner_id: int, group_id: int) -> bool:
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM friend_groups WHERE id = ? AND owner_id = ?",
                           (group_id, owner_id))
        if cur.rowcount:
            conn.execute(
                "DELETE FROM category_rules WHERE subject_type = 'group' AND subject_id = ?",
                (group_id,))
            conn.execute(
                "DELETE FROM activity_rules WHERE subject_type = 'group' AND subject_id = ?",
                (group_id,))
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_group_members(owner_id: int, group_id: int, member_ids: Iterable[int]) -> bool:
    """Replace the members of a group; non-friends are silently dropped."""
    allowed = friend_ids(owner_id)
    wanted = {m for m in member_ids if m in allowed}
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM friend_groups WHERE id = ? AND owner_id = ?",
                            (group_id, owner_id)).fetchone():
            conn.rollback()
            return False
        conn.execute("DELETE FROM friend_group_members WHERE group_id = ?", (group_id,))
        conn.executemany("INSERT INTO friend_group_members (group_id, member_id) VALUES (?, ?)",
                         [(group_id, m) for m in wanted])
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_member_groups(owner_id: int, member_id: int, group_ids: Iterable[int]) -> None:
    """Put friend *member_id* in exactly *group_ids* (of *owner_id*'s groups)."""
    own = {g["id"] for g in list_groups(owner_id)}
    wanted = {g for g in group_ids if g in own}
    conn = _get_conn()
    conn.execute(
        "DELETE FROM friend_group_members WHERE member_id = ? "
        "AND group_id IN (SELECT id FROM friend_groups WHERE owner_id = ?)", (member_id, owner_id))
    conn.executemany("INSERT INTO friend_group_members (group_id, member_id) VALUES (?, ?)",
                     [(g, member_id) for g in wanted])
    conn.commit()
    conn.close()


def viewer_group_ids(owner_id: int, viewer_id: int) -> list[int]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT m.group_id FROM friend_group_members m JOIN friend_groups g ON g.id = m.group_id "
        "WHERE g.owner_id = ? AND m.member_id = ?", (owner_id, viewer_id)).fetchall()
    conn.close()
    return [r["group_id"] for r in rows]


# ═══════════════════════════════════════════════════════ Rules ══

def _valid_subject(owner_id: int, subject_type: str, subject_id: int) -> bool:
    if subject_type == "all":
        return subject_id == 0
    if subject_type == "group":
        return get_group(owner_id, subject_id) is not None
    if subject_type == "friend":
        return subject_id in friend_ids(owner_id)
    return False


def category_rules(owner_id: int) -> dict[int, dict]:
    """``{category_id: {(subject_type, subject_id): level}}`` for the owner."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT r.category_id, r.subject_type, r.subject_id, r.level FROM category_rules r "
        "JOIN categories c ON c.id = r.category_id WHERE c.user_id = ?", (owner_id,)).fetchall()
    conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault(r["category_id"], {})[(r["subject_type"], r["subject_id"])] = r["level"]
    return out


def set_category_rules(owner_id: int, subject_type: str, subject_id: int,
                       levels: dict[int, Optional[str]]) -> bool:
    """Set (or with ``None`` remove) the subject's level for each category."""
    if subject_type not in SUBJECT_TYPES or not _valid_subject(owner_id, subject_type, subject_id):
        return False
    conn = _get_conn()
    own = {r["id"] for r in conn.execute("SELECT id FROM categories WHERE user_id = ?", (owner_id,))}
    for category_id, level in levels.items():
        if category_id not in own:
            continue
        if level is None:
            conn.execute(
                "DELETE FROM category_rules WHERE category_id = ? AND subject_type = ? "
                "AND subject_id = ?", (category_id, subject_type, subject_id))
        elif level in LEVELS:
            conn.execute(
                "INSERT INTO category_rules (category_id, subject_type, subject_id, level) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(category_id, subject_type, subject_id) "
                "DO UPDATE SET level = excluded.level",
                (category_id, subject_type, subject_id, level))
    conn.commit()
    conn.close()
    return True


def activity_rules(user_id: int, activity_ids: Optional[Iterable[int]] = None) -> dict[int, dict]:
    """``{activity_id: {(subject_type, subject_id): level}}`` set by *user_id*."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT activity_id, subject_type, subject_id, level FROM activity_rules WHERE user_id = ?",
        (user_id,)).fetchall()
    conn.close()
    wanted = set(activity_ids) if activity_ids is not None else None
    out: dict = {}
    for r in rows:
        if wanted is None or r["activity_id"] in wanted:
            out.setdefault(r["activity_id"], {})[(r["subject_type"], r["subject_id"])] = r["level"]
    return out


def clean_rule_list(owner_id: int, rules) -> Optional[list[tuple[str, int, str]]]:
    """Validate a JSON rule list ``[{subject_type, subject_id, level}]``.

    Returns the cleaned list or ``None`` if anything is malformed.
    """
    if not isinstance(rules, list) or len(rules) > 200:
        return None
    friends = friend_ids(owner_id)
    groups = {g["id"] for g in list_groups(owner_id)}
    seen = {}
    for r in rules:
        if not isinstance(r, dict):
            return None
        st, sid, level = r.get("subject_type"), r.get("subject_id", 0), r.get("level")
        if st not in SUBJECT_TYPES or level not in LEVELS or type(sid) is not int:
            return None
        if (st == "all" and sid != 0) or (st == "group" and sid not in groups) \
                or (st == "friend" and sid not in friends):
            return None
        seen[(st, sid)] = level
    return [(st, sid, level) for (st, sid), level in seen.items()]


def set_activity_rules(user_id: int, activity_id: int, rules: list[tuple[str, int, str]]) -> None:
    """Replace *user_id*'s rules for one activity (already validated)."""
    conn = _get_conn()
    conn.execute("DELETE FROM activity_rules WHERE activity_id = ? AND user_id = ?",
                 (activity_id, user_id))
    conn.executemany(
        "INSERT INTO activity_rules (activity_id, user_id, subject_type, subject_id, level) "
        "VALUES (?, ?, ?, ?, ?)", [(activity_id, user_id, st, sid, lv) for st, sid, lv in rules])
    conn.commit()
    conn.close()


# ═════════════════════════════════════════ Shared activities ══

def participants(activity_ids: Iterable[int]) -> dict[int, list[dict]]:
    ids = list(set(activity_ids))
    if not ids:
        return {}
    conn = _get_conn()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT p.activity_id, p.status, {USER_COLS} FROM activity_participants p "
        f"JOIN users u ON u.id = p.user_id WHERE p.activity_id IN ({marks}) "
        "ORDER BY u.display_name COLLATE NOCASE", ids).fetchall()
    conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault(r["activity_id"], []).append(
            {"user_id": r["id"], "username": r["username"], "display_name": r["display_name"],
             "status": r["status"]})
    return out


def set_invitees(owner_id: int, activity_id: int, user_ids: Iterable[int]) -> tuple[list, list]:
    """Make *user_ids* (friends only) the invitees of an owned activity.

    New ones become ``pending``; people no longer listed are removed.
    Returns ``(added_ids, removed_ids)``.
    """
    allowed = friend_ids(owner_id)
    wanted = {u for u in user_ids if u in allowed and u != owner_id}
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM activities WHERE id = ? AND user_id = ?",
                            (activity_id, owner_id)).fetchone():
            conn.rollback()
            return [], []
        current = {r["user_id"] for r in conn.execute(
            "SELECT user_id FROM activity_participants WHERE activity_id = ?", (activity_id,))}
        added, removed = sorted(wanted - current), sorted(current - wanted)
        now = _now()
        conn.executemany(
            "INSERT INTO activity_participants (activity_id, user_id, status, invited_at) "
            "VALUES (?, ?, 'pending', ?)", [(activity_id, u, now) for u in added])
        for u in removed:
            conn.execute("DELETE FROM activity_participants WHERE activity_id = ? AND user_id = ?",
                         (activity_id, u))
            conn.execute("DELETE FROM activity_rules WHERE activity_id = ? AND user_id = ?",
                         (activity_id, u))
        conn.commit()
        return added, removed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_participation(user_id: int, activity_id: int) -> Optional[dict]:
    """The activity (with owner info) if *user_id* was invited to it."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT a.id, a.user_id AS owner_id, a.title, a.day, a.start_time, a.end_time, "
        "       a.week_type, a.date, a.valid_from, a.valid_until, "
        "       p.status, p.category_id AS my_category_id, "
        "       o.username AS owner_username, o.display_name AS owner_name, "
        "       o.week1_start AS owner_week1_start "
        "FROM activity_participants p JOIN activities a ON a.id = p.activity_id "
        "JOIN users o ON o.id = a.user_id WHERE p.user_id = ? AND p.activity_id = ?",
        (user_id, activity_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def shared_activities(user_id: int, statuses=("accepted", "pending")) -> list[dict]:
    """Activities other people invited *user_id* to."""
    marks = ",".join("?" * len(statuses))
    conn = _get_conn()
    rows = conn.execute(
        "SELECT a.id, a.user_id AS owner_id, a.title, a.day, a.start_time, a.end_time, "
        "       a.week_type, a.date, a.valid_from, a.valid_until, a.category_id AS owner_category_id, "
        "       p.status, p.category_id AS my_category_id, p.invited_at, "
        "       o.username AS owner_username, o.display_name AS owner_name, "
        "       o.week1_start AS owner_week1_start "
        "FROM activity_participants p JOIN activities a ON a.id = p.activity_id "
        "JOIN users o ON o.id = a.user_id "
        f"WHERE p.user_id = ? AND p.status IN ({marks}) AND o.is_active = 1 "
        "ORDER BY a.date IS NOT NULL, a.date, a.day, a.start_time",
        (user_id, *statuses)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def respond_invite(user_id: int, activity_id: int, accept: bool,
                   category_id: Optional[int] = None) -> Optional[dict]:
    """Accept / decline an invite; returns the activity or ``None``."""
    act = get_participation(user_id, activity_id)
    if act is None:
        return None
    now = _now()
    conn = _get_conn()
    if category_id is not None and not conn.execute(
            "SELECT 1 FROM categories WHERE id = ? AND user_id = ?",
            (category_id, user_id)).fetchone():
        category_id = None
    conn.execute(
        "UPDATE activity_participants SET status = ?, responded_at = ?, "
        "category_id = COALESCE(?, category_id) WHERE activity_id = ? AND user_id = ?",
        ("accepted" if accept else "declined", now, category_id, activity_id, user_id))
    if act["status"] != ("accepted" if accept else "declined"):
        if accept:
            kind = "invite_accepted"
        else:
            kind = "participant_left" if act["status"] == "accepted" else "invite_declined"
        conn.execute(
            "INSERT INTO notifications (user_id, kind, actor_id, activity_id, text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (act["owner_id"], kind, user_id, activity_id, act["title"], now))
    conn.commit()
    conn.close()
    return act


def set_participant_category(user_id: int, activity_id: int, category_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE activity_participants SET category_id = ? WHERE activity_id = ? AND user_id = ? "
        "AND EXISTS (SELECT 1 FROM categories WHERE id = ? AND user_id = ?)",
        (category_id, activity_id, user_id, category_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def pending_invites(user_id: int) -> list[dict]:
    return shared_activities(user_id, statuses=("pending",))


# ═══════════════════════════════════════════════ Notifications ══

NOTIFY_KINDS = (
    "friend_accepted", "friend_joined", "invite_accepted", "invite_declined",
    "activity_invite", "activity_changed", "activity_cancelled", "participant_left",
)


def notify(user_ids: Iterable[int], kind: str, actor_id: Optional[int],
           activity_id: Optional[int] = None, text: str = "") -> None:
    rows = [(u, kind, actor_id, activity_id, text[:200], _now()) for u in set(user_ids)
            if u != actor_id]
    if not rows:
        return
    conn = _get_conn()
    conn.executemany(
        "INSERT INTO notifications (user_id, kind, actor_id, activity_id, text, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    # Keep the inbox bounded: the newest 200 per user.
    for u in {r[0] for r in rows}:
        conn.execute(
            "DELETE FROM notifications WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 200)", (u, u))
    conn.commit()
    conn.close()


def list_notifications(user_id: int, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT n.id, n.kind, n.activity_id, n.text, n.created_at, n.read_at, "
        "       u.username AS actor_username, u.display_name AS actor_name "
        "FROM notifications n LEFT JOIN users u ON u.id = n.actor_id "
        "WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?", (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_notifications_read(user_id: int) -> None:
    conn = _get_conn()
    conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                 (_now(), user_id))
    conn.commit()
    conn.close()


def badge_count(user_id: int) -> int:
    """Unread notifications + things waiting for an answer."""
    conn = _get_conn()
    n = conn.execute(
        "SELECT (SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL) "
        "     + (SELECT COUNT(*) FROM friendships f JOIN users u ON u.id = f.requester_id "
        "         WHERE f.addressee_id = ? AND f.status = 'pending' AND u.is_active = 1) "
        "     + (SELECT COUNT(*) FROM activity_participants p "
        "         JOIN activities a ON a.id = p.activity_id JOIN users o ON o.id = a.user_id "
        "         WHERE p.user_id = ? AND p.status = 'pending' AND o.is_active = 1)",
        (user_id, user_id, user_id)).fetchone()[0]
    conn.close()
    return n


# ════════════════════════════════════════════════ Friend links ══

def create_friend_link(user_id: int, code: str, note: str, max_uses: int,
                       expires_at: Optional[int]) -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO friend_links (user_id, code, note, max_uses, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (user_id, code, note, max_uses, expires_at, _now()))
    conn.commit()
    conn.close()
    return cur.lastrowid


def list_friend_links(user_id: int) -> list[dict]:
    from db import _with_status
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM friend_links WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                        (user_id,)).fetchall()
    conn.close()
    now = _now()
    return [_with_status(dict(r), now) for r in rows]


def get_usable_friend_link(code: str) -> Optional[dict]:
    """The link and its owner, if the code works right now."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT l.*, u.username, u.display_name FROM friend_links l "
        "JOIN users u ON u.id = l.user_id "
        "WHERE l.code = ? AND l.revoked = 0 AND l.uses < l.max_uses "
        "AND (l.expires_at IS NULL OR l.expires_at > ?) AND u.is_active = 1",
        (code, _now())).fetchone()
    conn.close()
    return dict(row) if row else None


def use_friend_link(code: str, user_id: int) -> Optional[str]:
    """An existing account accepts a friend link.

    Returns ``friends`` / ``already`` / ``own`` / ``blocked`` or ``None``
    when the link is not usable.
    """
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        link = conn.execute(
            "SELECT l.* FROM friend_links l JOIN users u ON u.id = l.user_id "
            "WHERE l.code = ? AND l.revoked = 0 AND l.uses < l.max_uses "
            "AND (l.expires_at IS NULL OR l.expires_at > ?) AND u.is_active = 1",
            (code, now)).fetchone()
        if link is None:
            conn.rollback()
            return None
        owner = link["user_id"]
        if owner == user_id:
            conn.rollback()
            return "own"
        if conn.execute(
                "SELECT 1 FROM blocks WHERE (blocker_id = ? AND blocked_id = ?) "
                "OR (blocker_id = ? AND blocked_id = ?)", (owner, user_id, user_id, owner)).fetchone():
            conn.rollback()
            return "blocked"
        lo, hi = _pair(owner, user_id)
        row = conn.execute("SELECT * FROM friendships WHERE user_lo = ? AND user_hi = ?",
                           (lo, hi)).fetchone()
        if row and row["status"] == "accepted":
            conn.rollback()
            return "already"
        if row:
            conn.execute("UPDATE friendships SET status = 'accepted', accepted_at = ? WHERE id = ?",
                         (now, row["id"]))
        else:
            conn.execute(
                "INSERT INTO friendships (requester_id, addressee_id, user_lo, user_hi, status, "
                "created_at, accepted_at) VALUES (?, ?, ?, ?, 'accepted', ?, ?)",
                (owner, user_id, lo, hi, now, now))
        conn.execute("UPDATE friend_links SET uses = uses + 1 WHERE id = ?", (link["id"],))
        conn.execute(
            "INSERT INTO notifications (user_id, kind, actor_id, created_at) "
            "VALUES (?, 'friend_joined', ?, ?)", (owner, user_id, now))
        conn.commit()
        return "friends"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_friend_link(user_id: int, link_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("UPDATE friend_links SET revoked = 1 WHERE id = ? AND user_id = ?",
                       (link_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_friend_link(user_id: int, link_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM friend_links WHERE id = ? AND user_id = ?", (link_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ═════════════════════════════════════════════════ Share links ══

SHARE_FIELDS = ("label", "preset", "window_type", "range_from", "range_to", "rolling_days",
                "days_list", "expires_at", "show_status")


def _share_row(row) -> dict:
    link = dict(row)
    link["days"] = json.loads(link["days_list"]) if link.get("days_list") else []
    link["has_pin"] = bool(link.get("pin_hash"))
    return link


def create_share_link(user_id: int, token: str, fields: dict, pin_hash: Optional[str],
                      rules: dict[int, str]) -> int:
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cols = ", ".join(SHARE_FIELDS)
        cur = conn.execute(
            f"INSERT INTO share_links (user_id, token, pin_hash, created_at, {cols}) "
            f"VALUES (?, ?, ?, ?, {', '.join('?' * len(SHARE_FIELDS))})",
            (user_id, token, pin_hash, _now(), *(fields[f] for f in SHARE_FIELDS)))
        link_id = cur.lastrowid
        _write_share_rules(conn, user_id, link_id, rules)
        conn.commit()
        return link_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_share_link(user_id: int, link_id: int, fields: dict, rules: dict[int, str],
                      pin_hash: Optional[str] = None, clear_pin: bool = False) -> bool:
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        assignments = ", ".join(f"{f} = ?" for f in SHARE_FIELDS)
        params = [fields[f] for f in SHARE_FIELDS]
        if pin_hash is not None:
            assignments += ", pin_hash = ?"
            params.append(pin_hash)
        elif clear_pin:
            assignments += ", pin_hash = NULL"
        cur = conn.execute(f"UPDATE share_links SET {assignments} WHERE id = ? AND user_id = ?",
                           (*params, link_id, user_id))
        if cur.rowcount:
            conn.execute("DELETE FROM share_link_rules WHERE link_id = ?", (link_id,))
            _write_share_rules(conn, user_id, link_id, rules)
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _write_share_rules(conn, user_id: int, link_id: int, rules: dict[int, str]) -> None:
    own = {r["id"] for r in conn.execute("SELECT id FROM categories WHERE user_id = ?", (user_id,))}
    conn.executemany(
        "INSERT INTO share_link_rules (link_id, category_id, level) VALUES (?, ?, ?)",
        [(link_id, c, lv) for c, lv in rules.items() if c in own and lv in LEVELS])


def list_share_links(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM share_links WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                        (user_id,)).fetchall()
    conn.close()
    return [_share_row(r) for r in rows]


def get_share_link(user_id: int, link_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM share_links WHERE id = ? AND user_id = ?",
                       (link_id, user_id)).fetchone()
    conn.close()
    return _share_row(row) if row else None


def get_share_by_token(token: str) -> Optional[dict]:
    """The link (whatever its state) and its owner, by token."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT l.*, u.is_active AS owner_active FROM share_links l "
        "JOIN users u ON u.id = l.user_id WHERE l.token = ?", (token,)).fetchone()
    conn.close()
    return _share_row(row) if row else None


def share_rules(link_id: int) -> dict[int, str]:
    conn = _get_conn()
    rows = conn.execute("SELECT category_id, level FROM share_link_rules WHERE link_id = ?",
                        (link_id,)).fetchall()
    conn.close()
    return {r["category_id"]: r["level"] for r in rows}


def record_share_view(link_id: int) -> None:
    conn = _get_conn()
    conn.execute("UPDATE share_links SET views = views + 1, last_viewed_at = ? WHERE id = ?",
                 (_now(), link_id))
    conn.commit()
    conn.close()


def set_share_revoked(user_id: int, link_id: int, revoked: bool) -> bool:
    conn = _get_conn()
    cur = conn.execute("UPDATE share_links SET revoked = ? WHERE id = ? AND user_id = ?",
                       (int(revoked), link_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def regenerate_share_token(user_id: int, link_id: int, token: str) -> bool:
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE share_links SET token = ?, views = 0, last_viewed_at = NULL "
        "WHERE id = ? AND user_id = ?", (token, link_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def delete_share_link(user_id: int, link_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM share_links WHERE id = ? AND user_id = ?", (link_id, user_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def count_rows(table: str, user_col: str, user_id: int) -> int:
    """Per-user row count for the limits ("max 30 groups" …)."""
    assert table in {"categories", "friend_groups", "friend_links", "share_links"}
    assert user_col in {"user_id", "owner_id"}
    conn = _get_conn()
    n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {user_col} = ?", (user_id,)).fetchone()[0]
    conn.close()
    return n


IntegrityError = sqlite3.IntegrityError
