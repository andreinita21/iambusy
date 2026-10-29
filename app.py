"""
IamBusy — Flask application entry point.

Thin HTTP layer that delegates all schedule logic to :mod:`schedule_engine`
and reads configuration from :mod:`schedule_config`.
Backed by a SQLite database with one schedule per account; every page and
API route requires a logged-in session (see :mod:`auth`).
"""

import os
import re
from datetime import date, datetime, timedelta
from flask import Flask, g, render_template, request, jsonify

try:
    import schedule_config as config
except ModuleNotFoundError as exc:
    if exc.name != "schedule_config":
        raise
    raise SystemExit(
        "schedule_config.py is missing — run ./setup.sh "
        "(or copy schedule_config.example.py to schedule_config.py)."
    )

DEFAULT_WEEK1_START = config.ACADEMIC_WEEK1_START
APP_HOST = getattr(config, "APP_HOST", "0.0.0.0")
APP_PORT = getattr(config, "APP_PORT", 2027)
DEBUG = getattr(config, "DEBUG", False)
SESSION_DAYS = getattr(config, "SESSION_DAYS", 30)
SHORT_SESSION_HOURS = getattr(config, "SHORT_SESSION_HOURS", 12)
COOKIE_SECURE = getattr(config, "COOKIE_SECURE", False)
TRUSTED_PROXIES = getattr(config, "TRUSTED_PROXIES", [])

from schedule_engine import (
    DAY_DISPLAY,
    DAY_SHORT,
    DAYS,
    academic_week_number,
    format_date_ro,
    relative_day_ro,
    build_day_timeline,
    busy_segments,
    compute_status,
    day_summary,
    find_next_activity,
    free_windows,
    is_odd_week,
    prepare_blocks_for_ui,
)
from db import (
    init_db,
    get_schedule,
    get_all_activities,
    get_activity,
    add_activity,
    count_activities,
    delete_activity,
    update_activity,
    find_conflicts,
)
import auth
from auth import login_required

APP_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=auth.load_secret_key(APP_DIR),
    SESSION_COOKIE_NAME="iambusy_csrf",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_DAYS),
    MAX_CONTENT_LENGTH=64 * 1024,  # nothing here needs a big request body
)
auth.configure(
    session_days=SESSION_DAYS,
    short_session_hours=SHORT_SESSION_HOURS,
    cookie_secure=COOKIE_SECURE,
    trusted_proxies=TRUSTED_PROXIES,
    default_week1_start=DEFAULT_WEEK1_START,
)

# ── Initialise database on import ────────────────────────────
init_db()

app.before_request(auth.load_current_user)
app.before_request(auth.csrf_protect)
app.register_blueprint(auth.bp)
app.jinja_env.globals["csrf_token"] = auth.csrf_token

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    "frame-ancestors 'none'",
])


@app.after_request
def harden_response(response):
    response = auth.apply_auth_cookie(response)
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if COOKIE_SECURE:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.endpoint != "static":
        # Private pages must not be kept by the browser or a shared cache.
        response.headers["Cache-Control"] = "no-store"
    return response


# ═════════════════════════════════════════════════ Validation ══

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
WEEK_TYPES = ("odd", "even", "both")
TITLE_MAX = 120
MAX_ACTIVITIES = 500  # per account


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _clean_activity(data, current=None):
    """Validate activity fields; missing ones fall back to *current*.

    Returns ``(fields, None)`` or ``(None, error message)``.
    """
    fields = {}
    for name in ("title", "day", "start_time", "end_time", "week_type"):
        value = data.get(name)
        if value is None and current is not None:
            value = current[name]
        if value is None and name == "week_type":
            value = "both"
        if not isinstance(value, str) or not value.strip():
            return None, "Missing required fields"
        fields[name] = value.strip()

    if len(fields["title"]) > TITLE_MAX or any(ord(c) < 32 for c in fields["title"]):
        return None, f"Title must be at most {TITLE_MAX} characters"
    if fields["day"] not in DAYS:
        return None, "Invalid day"
    if fields["week_type"] not in WEEK_TYPES:
        return None, "Invalid week type"
    if not TIME_RE.match(fields["start_time"]) or not TIME_RE.match(fields["end_time"]):
        return None, "Times must be HH:MM"
    if fields["end_time"] <= fields["start_time"]:
        return None, "End time must be after start time"
    return fields, None


def _week1_start() -> date:
    """First day of week 1 for the logged-in user."""
    try:
        return date.fromisoformat(g.user["week1_start"])
    except (TypeError, ValueError):
        return DEFAULT_WEEK1_START


# ═══════════════════════════════════════════════════════ Pages ══

@app.route("/")
@login_required
def index():
    """Render the daily schedule view.

    Query parameters
    ----------------
    date : str, optional
        ISO-format date (``YYYY-MM-DD``) to view.  Defaults to today.
    """
    # ── resolve the target date ──────────────────────────────
    date_str = request.args.get("date")
    if date_str:
        try:
            view_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            view_date = datetime.now().date()
    else:
        view_date = datetime.now().date()

    now = datetime.now()
    today = now.date()
    user_id = g.user["id"]
    USER_NAME = g.user["display_name"]
    ACADEMIC_WEEK1_START = _week1_start()

    # ── schedule selection (from DB) ─────────────────────────
    schedule_odd_db = get_schedule(user_id, "odd")
    schedule_even_db = get_schedule(user_id, "even")

    def schedule_for(d):
        """Schedule dict for date *d* — empty before the academic year."""
        if d < ACADEMIC_WEEK1_START:
            return {}
        return schedule_odd_db if is_odd_week(ACADEMIC_WEEK1_START, d) else schedule_even_db

    week_is_odd = is_odd_week(ACADEMIC_WEEK1_START, view_date)
    week_num = academic_week_number(ACADEMIC_WEEK1_START, view_date)
    timeline = build_day_timeline(schedule_for(view_date), view_date)

    # ── next-activity look-ahead ─────────────────────────────
    next_act = find_next_activity(
        schedule_odd_db, schedule_even_db, ACADEMIC_WEEK1_START, view_date,
    )

    # ── status derivation ────────────────────────────────────
    is_today = view_date == today
    if is_today:
        status_main, status_sub, current_block = compute_status(
            now, timeline, USER_NAME, next_activity=next_act,
        )
    else:
        n_courses = sum(1 for b in timeline if b["type"] == "course")
        status_main = f"Program pentru {relative_day_ro(view_date, today)}"
        if view_date.year != today.year:
            status_main += f" {view_date.year}"
        status_sub = (
            "Nicio activitate programată." if n_courses == 0
            else f"{n_courses} {'activitate' if n_courses == 1 else 'activități'}."
        )
        current_block = None

    if current_block is None or not is_today:
        state = "off"
    elif current_block["type"] == "course":
        state = "busy"
    else:
        state = "free"

    # ── week label ───────────────────────────────────────────
    if week_num <= 0:
        week_label = f"Vacanță · începe pe {format_date_ro(ACADEMIC_WEEK1_START, long=True)}"
    else:
        week_label = f"Săptămâna {week_num} · {'impară' if week_is_odd else 'pară'}"

    # ── week strip (Mon–Sun of the viewed week) ──────────────
    monday = view_date - timedelta(days=view_date.weekday())
    week_strip = []
    for i in range(7):
        d = monday + timedelta(days=i)
        entries = schedule_for(d).get(DAYS[i], [])
        week_strip.append({
            "short": DAY_SHORT[i],
            "day": d.day,
            "count": len(entries),
            "segments": busy_segments(entries),
            "link": f"?date={d.isoformat()}",
            "is_today": d == today,
            "is_selected": d == view_date,
            "is_weekend": i >= 5,
            "label": f"{DAY_DISPLAY[DAYS[i]]}, {format_date_ro(d, long=True)}",
        })

    # ── template context ─────────────────────────────────────
    blocks_for_ui = prepare_blocks_for_ui(timeline, current_block)
    prev_date = view_date - timedelta(days=1)
    next_date = view_date + timedelta(days=1)
    n_courses = sum(1 for b in blocks_for_ui if b["type"] == "course")

    context = {
        "user_name": USER_NAME,
        "username": g.user["username"],
        "is_admin": bool(g.user["is_admin"]),
        "state": state,
        "is_today": is_today,
        "has_courses": n_courses > 0,
        "n_courses": n_courses,
        "free_windows": free_windows(timeline),
        "week_label": week_label,
        "week_num": week_num,
        "week_parity": "odd" if week_is_odd else "even",
        "day_key": DAYS[view_date.weekday()],
        "today_label": DAY_DISPLAY[DAYS[view_date.weekday()]],
        "view_date_str": format_date_ro(view_date, long=True),
        "view_date_iso": view_date.strftime("%Y-%m-%d"),
        "today_iso": today.strftime("%Y-%m-%d"),
        "now": now.strftime("%H:%M:%S"),
        "status_main": status_main,
        "status_sub": status_sub,
        "current_end_iso": current_block["end_dt"].isoformat() if (current_block and is_today) else "",
        "blocks": blocks_for_ui,
        "week_strip": week_strip,
        "prev_link": f"?date={prev_date.strftime('%Y-%m-%d')}",
        "next_link": f"?date={next_date.strftime('%Y-%m-%d')}",
    }
    return render_template("index.html", context=context)


@app.route("/manage")
@login_required
def manage():
    """Render the schedule management page.

    Optional query parameters (``day``, ``start``, ``end``, ``week``) are
    read client-side to pre-fill the "add activity" form — this is how the
    daily view's "schedule something in this gap" links work.
    """
    week_key = "odd" if is_odd_week(_week1_start(), datetime.now().date()) else "even"
    return render_template(
        "manage.html",
        days=DAYS,
        day_display=DAY_DISPLAY,
        current_week=week_key,
        user_name=g.user["display_name"],
    )


# ═══════════════════════════════════════════════════ JSON API ══

@app.route("/api/days", methods=["GET"])
@login_required
def api_days():
    """Per-day summaries for a date range (inclusive) — feeds the month calendar.

    Query parameters
    ----------------
    from, to : str
        ISO dates.  The range is capped at 62 days.
    """
    try:
        start = datetime.strptime(request.args.get("from", ""), "%Y-%m-%d").date()
        end = datetime.strptime(request.args.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "from/to must be YYYY-MM-DD"}), 400
    if end < start:
        return jsonify({"error": "to must be after from"}), 400
    if (end - start).days > 62:
        return jsonify({"error": "Range too large (max 62 days)"}), 400

    ACADEMIC_WEEK1_START = _week1_start()
    schedule_odd_db = get_schedule(g.user["id"], "odd")
    schedule_even_db = get_schedule(g.user["id"], "even")

    days = []
    d = start
    while d <= end:
        if d < ACADEMIC_WEEK1_START:
            schedule = {}
        else:
            schedule = schedule_odd_db if is_odd_week(ACADEMIC_WEEK1_START, d) else schedule_even_db
        days.append(day_summary(schedule, d, ACADEMIC_WEEK1_START))
        d += timedelta(days=1)

    return jsonify({
        "today": datetime.now().date().isoformat(),
        "academic_start": ACADEMIC_WEEK1_START.isoformat(),
        "days": days,
    })

@app.route("/api/activities", methods=["GET"])
@login_required
def api_list_activities():
    """Return all activities of the logged-in user as JSON."""
    return jsonify(get_all_activities(g.user["id"]))


@app.route("/api/activities", methods=["POST"])
@login_required
def api_add_activity():
    """Add a new activity. Returns conflicts if any exist."""
    data = _json_body()
    if data is None:
        return jsonify({"error": "Invalid JSON body"}), 400
    fields, error = _clean_activity(data)
    if error:
        return jsonify({"error": error}), 400

    user_id = g.user["id"]
    if count_activities(user_id) >= MAX_ACTIVITIES:
        return jsonify({"error": f"Limit of {MAX_ACTIVITIES} activities reached"}), 400

    # Check conflicts
    conflicts = find_conflicts(
        user_id, fields["day"], fields["start_time"], fields["end_time"], fields["week_type"],
    )
    if conflicts and data.get("force") is not True:
        return jsonify({"conflicts": conflicts, "needs_resolution": True}), 409

    new_id = add_activity(user_id, **fields)
    return jsonify({"id": new_id, "success": True}), 201


@app.route("/api/activities/<int:activity_id>", methods=["PUT"])
@login_required
def api_update_activity(activity_id):
    """Update (move) an activity. Returns conflicts if any exist."""
    data = _json_body()
    if data is None:
        return jsonify({"error": "Invalid JSON body"}), 400

    user_id = g.user["id"]
    current = get_activity(user_id, activity_id)
    if current is None:
        return jsonify({"error": "Activity not found"}), 404

    # Missing fields keep their current values
    fields, error = _clean_activity(data, current=current)
    if error:
        return jsonify({"error": error}), 400

    # Check conflicts (exclude self)
    conflicts = find_conflicts(
        user_id, fields["day"], fields["start_time"], fields["end_time"],
        fields["week_type"], exclude_id=activity_id,
    )
    if conflicts and data.get("force") is not True:
        return jsonify({"conflicts": conflicts, "needs_resolution": True}), 409

    update_activity(user_id, activity_id, **fields)
    return jsonify({"success": True})


@app.route("/api/activities/<int:activity_id>", methods=["DELETE"])
@login_required
def api_delete_activity(activity_id):
    """Delete an activity by id."""
    if delete_activity(g.user["id"], activity_id):
        return jsonify({"success": True})
    return jsonify({"error": "Activity not found"}), 404


@app.route("/api/conflicts", methods=["POST"])
@login_required
def api_check_conflicts():
    """Pre-check for conflicts without saving anything."""
    data = _json_body()
    if data is None:
        return jsonify({"error": "Invalid JSON body"}), 400
    exclude_id = data.get("exclude_id")
    if exclude_id is not None and type(exclude_id) is not int:
        return jsonify({"error": "Invalid exclude_id"}), 400
    fields, error = _clean_activity({**data, "title": "-"})
    if error:
        return jsonify({"error": error}), 400

    conflicts = find_conflicts(
        g.user["id"], fields["day"], fields["start_time"], fields["end_time"],
        fields["week_type"], exclude_id=exclude_id,
    )
    return jsonify({"conflicts": conflicts})


if __name__ == "__main__":
    app.run(debug=DEBUG, host=APP_HOST, port=APP_PORT)
