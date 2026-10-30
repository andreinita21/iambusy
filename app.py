"""
IamBusy — Flask application entry point.

Thin HTTP layer that delegates all schedule logic to :mod:`schedule_engine`
and :mod:`calendar_data`, and reads configuration from :mod:`schedule_config`.
Backed by a SQLite database with one schedule per account; every page and
API route requires a logged-in session (see :mod:`auth`) except the public
share links (see :mod:`sharing`).
"""

import os
import re
from datetime import date, datetime, timedelta
from flask import Flask, g, render_template, request, jsonify
from werkzeug.exceptions import HTTPException

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
SITE_URL = getattr(config, "SITE_URL", "")
OPERATOR_NAME = getattr(config, "OPERATOR_NAME", "")
CONTACT_EMAIL = getattr(config, "CONTACT_EMAIL", "")

from schedule_engine import DAY_DISPLAY, DAYS, find_conflicts, is_odd_week
from db import (
    init_db,
    get_all_activities,
    get_activity,
    add_activity,
    count_activities,
    delete_activity,
    update_activity,
    get_category,
    list_categories,
)
import db_social
from visibility import DEFAULT_LEVEL
from calendar_data import (
    Calendar, Viewer, build_day_context, day_summaries, parse_range, parse_week1, view_date,
)
import auth
from auth import login_required
import public
import social
import sharing

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
    site_url=SITE_URL,
)
public.configure(site_url=SITE_URL, operator_name=OPERATOR_NAME, contact_email=CONTACT_EMAIL,
                 session_days=SESSION_DAYS, short_session_hours=SHORT_SESSION_HOURS)
social.configure(default_week1_start=DEFAULT_WEEK1_START)
sharing.configure(default_week1_start=DEFAULT_WEEK1_START, cookie_secure=COOKIE_SECURE)

# ── Initialise database on import ────────────────────────────
init_db()

app.before_request(public.canonical_redirect)
app.before_request(auth.load_current_user)
app.before_request(auth.csrf_protect)
app.register_blueprint(public.bp)
app.register_blueprint(auth.bp)
app.register_blueprint(social.bp)
app.register_blueprint(sharing.bp)
app.jinja_env.globals["csrf_token"] = auth.csrf_token
app.jinja_env.globals["asset"] = public.asset_url
app.context_processor(public.template_globals)
app.register_error_handler(HTTPException, public.handle_http_error)
app.register_error_handler(Exception, public.handle_unexpected_error)


@app.context_processor
def inject_badge():
    """Number on the friends button: requests, invites, unread news."""
    def badge():
        return db_social.badge_count(g.user["id"]) if g.get("user") else 0
    return {"social_badge": badge}


CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
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
    if request.endpoint in public.CACHEABLE_ENDPOINTS and response.status_code == 200:
        public.cache_public(response)
    elif request.endpoint != "static":
        # Private pages must not be kept by the browser or a shared cache.
        response.headers["Cache-Control"] = "no-store"
    return response


# ═════════════════════════════════════════════════ Validation ══

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WEEK_TYPES = ("odd", "even", "both")
TITLE_MAX = 120
MAX_ACTIVITIES = 500  # per account
MAX_INVITEES = 50


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _clean_date(value):
    """ISO date string → itself, ``None``/"" → ``None``, anything else → ``False``."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return False
    return value if 2000 <= d.year <= 2100 else False


def _clean_activity(data, current=None):
    """Validate activity fields; missing ones fall back to *current*.

    A ``date`` makes the activity a one-off event on that day (its weekday
    and week type are derived); without it the activity repeats weekly,
    optionally only between ``valid_from`` and ``valid_until``.

    Returns ``(fields, None)`` or ``(None, error message)``.
    """
    fields = {}
    for name in ("date", "valid_from", "valid_until"):
        value = data[name] if name in data else (current or {}).get(name)
        cleaned = _clean_date(value)
        if cleaned is False:
            return None, "Dates must be YYYY-MM-DD"
        fields[name] = cleaned

    if fields["date"]:
        fields["day"] = DAYS[date.fromisoformat(fields["date"]).weekday()]
        fields["week_type"] = "both"
        fields["valid_from"] = fields["valid_until"] = None

    for name in ("title", "day", "start_time", "end_time", "week_type"):
        if name in fields:
            continue
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
    if fields["valid_from"] and fields["valid_until"] and fields["valid_until"] < fields["valid_from"]:
        return None, "The repeat range ends before it starts"

    category_id = data.get("category_id", (current or {}).get("category_id"))
    if category_id is not None:
        if type(category_id) is not int or get_category(g.user["id"], category_id) is None:
            return None, "Invalid category"
    fields["category_id"] = category_id
    return fields, None


def _clean_extras(data):
    """Optional ``rules`` / ``invitees`` of an activity payload."""
    rules = invitees = None
    if "rules" in data:
        rules = db_social.clean_rule_list(g.user["id"], data["rules"])
        if rules is None:
            return None, None, "Invalid visibility rules"
    if "invitees" in data:
        raw = data["invitees"]
        if not isinstance(raw, list) or len(raw) > MAX_INVITEES or \
                any(type(u) is not int for u in raw):
            return None, None, "Invalid invitees"
        invitees = raw
    return rules, invitees, None


def _week1_start() -> date:
    """First day of week 1 for the logged-in user."""
    return parse_week1(g.user["week1_start"], DEFAULT_WEEK1_START)


def _parse_view_date() -> date:
    return view_date(request.args)


def _rules_json(rules: dict) -> list[dict]:
    return [{"subject_type": st, "subject_id": sid, "level": lv}
            for (st, sid), lv in sorted(rules.items())]


def _conflict_json(act: dict) -> dict:
    keys = ("id", "title", "day", "start_time", "end_time", "week_type", "date")
    return {k: act.get(k) for k in keys}


# ═══════════════════════════════════════════════════════ Pages ══

@app.route("/")
def index():
    """Render the daily schedule view (the public home page when logged out).

    Query parameters
    ----------------
    date : str, optional
        ISO-format date (``YYYY-MM-DD``) to view.  Defaults to today.
    """
    if g.user is None:
        return public.landing()
    cal = Calendar(g.user, DEFAULT_WEEK1_START)
    context = build_day_context(cal, Viewer.owner(), _parse_view_date(), datetime.now(),
                                name=g.user["display_name"])
    context.update(
        mode="own",
        username=g.user["username"],
        is_admin=bool(g.user["is_admin"]),
        api_days="/api/days",
        legend=[{"name": c["name"], "color": c["color"]} for c in cal.categories.values()],
    )
    return render_template("index.html", context=context)


@app.route("/manage")
@login_required
def manage():
    """Render the schedule management page.

    Optional query parameters (``day``, ``start``, ``end``, ``week``,
    ``date``, ``invite``) are read client-side to pre-fill the "add
    activity" form — this is how the daily view's "schedule something in
    this gap" links and the free-time finder's "invite" buttons work.
    """
    today = datetime.now().date()
    week_key = "odd" if is_odd_week(_week1_start(), today) else "even"
    return render_template(
        "manage.html",
        days=DAYS,
        day_display=DAY_DISPLAY,
        current_week=week_key,
        today=today.isoformat(),
        week1_start=_week1_start().isoformat(),
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
    start, end, error = parse_range(request.args)
    if error:
        return jsonify({"error": error}), 400
    cal = Calendar(g.user, DEFAULT_WEEK1_START)
    return jsonify({
        "today": datetime.now().date().isoformat(),
        "academic_start": cal.week1.isoformat(),
        "days": day_summaries(cal, Viewer.owner(), start, end),
    })


@app.route("/api/activities", methods=["GET"])
@login_required
def api_list_activities():
    """Return all activities the logged-in user owns as JSON."""
    user_id = g.user["id"]
    acts = get_all_activities(user_id)
    rules = db_social.activity_rules(user_id, (a["id"] for a in acts))
    people = db_social.participants(a["id"] for a in acts)
    for a in acts:
        a.pop("user_id", None)
        a["rules"] = _rules_json(rules.get(a["id"], {}))
        a["participants"] = people.get(a["id"], [])
    return jsonify(acts)


@app.route("/api/manage/context", methods=["GET"])
@login_required
def api_manage_context():
    """Everything the editor needs besides the activities themselves."""
    user_id = g.user["id"]
    shared = db_social.shared_activities(user_id)
    rules = db_social.activity_rules(user_id, (s["id"] for s in shared))
    people = db_social.participants(s["id"] for s in shared)
    for s in shared:
        s["rules"] = _rules_json(rules.get(s["id"], {}))
        s["participants"] = people.get(s["id"], [])
        for key in ("owner_category_id", "invited_at"):
            s.pop(key, None)
    cat_rules = db_social.category_rules(user_id)
    categories = list_categories(user_id)
    for c in categories:
        c["friends_level"] = cat_rules.get(c["id"], {}).get(("all", 0)) or DEFAULT_LEVEL
    return jsonify({
        "categories": categories,
        "friends": [{k: f[k] for k in ("id", "username", "display_name")}
                    for f in db_social.list_friends(user_id)],
        "groups": [{k: gr[k] for k in ("id", "name", "color")}
                   for gr in db_social.list_groups(user_id)],
        "shared": shared,
        "week1_start": _week1_start().isoformat(),
        "today": datetime.now().date().isoformat(),
    })


def _apply_extras(activity_id: int, rules, invitees) -> None:
    # New invitees need no notification: pending invites are listed (and
    # counted in the badge) straight from the participants table.
    user_id = g.user["id"]
    if rules is not None:
        db_social.set_activity_rules(user_id, activity_id, rules)
    if invitees is not None:
        db_social.set_invitees(user_id, activity_id, invitees)


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
    rules, invitees, error = _clean_extras(data)
    if error:
        return jsonify({"error": error}), 400

    user_id = g.user["id"]
    if count_activities(user_id) >= MAX_ACTIVITIES:
        return jsonify({"error": f"Limit of {MAX_ACTIVITIES} activities reached"}), 400

    conflicts = find_conflicts(fields, get_all_activities(user_id), _week1_start())
    if conflicts and data.get("force") is not True:
        return jsonify({"conflicts": [_conflict_json(c) for c in conflicts],
                        "needs_resolution": True}), 409

    new_id = add_activity(user_id, fields.pop("title"), fields.pop("day"),
                          fields.pop("start_time"), fields.pop("end_time"),
                          fields.pop("week_type"), **fields)
    _apply_extras(new_id, rules, invitees)
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
    rules, invitees, error = _clean_extras(data)
    if error:
        return jsonify({"error": error}), 400

    conflicts = find_conflicts(fields, get_all_activities(user_id), _week1_start(),
                               exclude_id=activity_id)
    if conflicts and data.get("force") is not True:
        return jsonify({"conflicts": [_conflict_json(c) for c in conflicts],
                        "needs_resolution": True}), 409

    update_activity(user_id, activity_id, fields.pop("title"), fields.pop("day"),
                    fields.pop("start_time"), fields.pop("end_time"), fields.pop("week_type"),
                    **fields)
    _apply_extras(activity_id, rules, invitees)

    when = ("title", "day", "start_time", "end_time", "week_type", "date", "valid_from",
            "valid_until")
    updated = get_activity(user_id, activity_id)
    if any(updated[k] != current[k] for k in when):
        people = db_social.participants([activity_id]).get(activity_id, [])
        db_social.notify([p["user_id"] for p in people if p["status"] != "declined"],
                         "activity_changed", user_id, activity_id, updated["title"])
    return jsonify({"success": True})


@app.route("/api/activities/<int:activity_id>", methods=["DELETE"])
@login_required
def api_delete_activity(activity_id):
    """Delete an activity by id."""
    user_id = g.user["id"]
    current = get_activity(user_id, activity_id)
    people = db_social.participants([activity_id]).get(activity_id, [])
    if current and delete_activity(user_id, activity_id):
        db_social.notify([p["user_id"] for p in people if p["status"] != "declined"],
                         "activity_cancelled", user_id, None, current["title"])
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

    conflicts = find_conflicts(fields, get_all_activities(g.user["id"]), _week1_start(),
                               exclude_id=exclude_id)
    return jsonify({"conflicts": [_conflict_json(c) for c in conflicts]})


# ── Activities other people invited me to ────────────────────

@app.route("/api/shared/<int:activity_id>", methods=["PUT"])
@login_required
def api_update_shared(activity_id):
    """My category and my visibility rules for an activity I was invited to."""
    data = _json_body()
    if data is None:
        return jsonify({"error": "Invalid JSON body"}), 400
    user_id = g.user["id"]
    if db_social.get_participation(user_id, activity_id) is None:
        return jsonify({"error": "Activity not found"}), 404
    if "category_id" in data:
        if type(data["category_id"]) is not int or \
                not db_social.set_participant_category(user_id, activity_id, data["category_id"]):
            return jsonify({"error": "Invalid category"}), 400
    if "rules" in data:
        rules = db_social.clean_rule_list(user_id, data["rules"])
        if rules is None:
            return jsonify({"error": "Invalid visibility rules"}), 400
        db_social.set_activity_rules(user_id, activity_id, rules)
    return jsonify({"success": True})


@app.route("/api/shared/<int:activity_id>/respond", methods=["POST"])
@login_required
def api_respond_shared(activity_id):
    data = _json_body()
    if data is None or type(data.get("accept")) is not bool:
        return jsonify({"error": "Invalid JSON body"}), 400
    category_id = data.get("category_id")
    if category_id is not None and type(category_id) is not int:
        return jsonify({"error": "Invalid category"}), 400
    if db_social.respond_invite(g.user["id"], activity_id, data["accept"], category_id) is None:
        return jsonify({"error": "Activity not found"}), 404
    return jsonify({"success": True})


@app.route("/api/shared/<int:activity_id>/leave", methods=["POST"])
@login_required
def api_leave_shared(activity_id):
    act = db_social.get_participation(g.user["id"], activity_id)
    if act is None:
        return jsonify({"error": "Activity not found"}), 404
    db_social.respond_invite(g.user["id"], activity_id, accept=False)
    db_social.set_activity_rules(g.user["id"], activity_id, [])
    return jsonify({"success": True})


if __name__ == "__main__":
    app.run(debug=DEBUG, host=APP_HOST, port=APP_PORT)
