"""
Account maintenance from the command line (run on the server itself).

    python3 accounts.py create-admin [USERNAME]     create an administrator
    python3 accounts.py reset-password USERNAME     set a new password, log the user out
    python3 accounts.py import-schedule USERNAME OLD_DB
                                                    copy the activities of a single-user
                                                    IamBusy ``schedule.db`` into an account
    python3 accounts.py list                        show all accounts

Passwords are asked for interactively (or read from stdin when piped) —
never pass them as arguments, they would end up in the shell history and
in the process list.
"""

import getpass
import sqlite3
import sys

import schedule_config as config
import auth
import db


def _read_password() -> str:
    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if getpass.getpass("Repeat password: ") != password:
            sys.exit("Passwords do not match.")
        return password
    return sys.stdin.readline().rstrip("\n")


def _checked_password(username: str) -> str:
    password = _read_password()
    error = auth.validate_password(password, username)
    if error:
        sys.exit(f"Password rejected: {error}")
    return password


def create_admin(username: str) -> None:
    username = auth.normalize_username(username)
    error = auth.validate_username(username)
    if error:
        sys.exit(error)
    if db.get_user_by_username(username):
        sys.exit(f"Account {username!r} already exists — use reset-password instead.")
    password = _checked_password(username)
    db.create_user(
        username=username,
        display_name=username.capitalize(),
        password_hash=auth.hash_password(password),
        week1_start=config.ACADEMIC_WEEK1_START.isoformat(),
        is_admin=True,
    )
    print(f"Administrator {username!r} created.")


def reset_password(username: str) -> None:
    user = db.get_user_by_username(auth.normalize_username(username))
    if user is None:
        sys.exit(f"No account named {username!r}.")
    password = _checked_password(user["username"])
    db.update_user(user["id"], password_hash=auth.hash_password(password))
    dropped = db.delete_user_sessions(user["id"])
    db.clear_attempts(f"login-user:{user['username']}")
    print(f"Password changed for {user['username']!r}; {dropped} device(s) logged out.")


def import_schedule(username: str, old_db: str) -> None:
    user = db.get_user_by_username(auth.normalize_username(username))
    if user is None:
        sys.exit(f"No account named {username!r}.")
    src = sqlite3.connect(f"file:{old_db}?mode=ro", uri=True)
    rows = src.execute(
        "SELECT title, day, start_time, end_time, week_type FROM activities"
    ).fetchall()
    src.close()
    existing = {
        (a["title"], a["day"], a["start_time"], a["end_time"], a["week_type"])
        for a in db.get_all_activities(user["id"])
    }
    added = 0
    for row in rows:
        if tuple(row) not in existing:
            db.add_activity(user["id"], *row)
            added += 1
    print(f"Imported {added} activities into {user['username']!r} "
          f"({len(rows) - added} already there).")


def list_accounts() -> None:
    for u in db.list_users():
        flags = ", ".join(
            flag for flag, on in (("admin", u["is_admin"]), ("disabled", not u["is_active"])) if on
        )
        print(f"{u['username']:<24} {u['display_name']:<24} "
              f"{u['activity_count']:>3} activities  {u['session_count']} devices  {flags}")


def main(argv: list) -> None:
    db.init_db()
    if len(argv) >= 1 and argv[0] == "create-admin" and len(argv) <= 2:
        create_admin(argv[1] if len(argv) == 2 else "admin")
    elif len(argv) == 2 and argv[0] == "reset-password":
        reset_password(argv[1])
    elif len(argv) == 3 and argv[0] == "import-schedule":
        import_schedule(argv[1], argv[2])
    elif argv == ["list"]:
        list_accounts()
    else:
        sys.exit(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
