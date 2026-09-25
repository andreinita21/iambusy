"""
IamBusy — Flask application entry point.

Thin HTTP layer that delegates all schedule logic to :mod:`schedule_engine`
and reads configuration from :mod:`schedule_config`.
Now backed by a SQLite database for CRUD management.
"""

from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for

from schedule_config import (
    ACADEMIC_WEEK1_START,
    APP_PORT,
    SCHEDULE_EVEN,
    SCHEDULE_ODD,
    USER_NAME,
)
from schedule_engine import (
    DAY_DISPLAY,
    DAY_SHORT,
    DAYS,
    academic_week_number,
    format_date_ro,
    relative_day_ro,
    build_day_timeline,
    compute_status,
    find_next_activity,
    is_odd_week,
    prepare_blocks_for_ui,
)
from db import (
    init_db,
    is_empty,
    seed_from_config,
    get_schedule,
    get_all_activities,
    add_activity,
    delete_activity,
    update_activity,
    find_conflicts,
)

app = Flask(__name__)

# ── Initialise database on import ────────────────────────────
init_db()
if is_empty():
    seed_from_config(SCHEDULE_ODD, SCHEDULE_EVEN)


# ═══════════════════════════════════════════════════════ Pages ══

@app.route("/")
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

    # ── schedule selection (from DB) ─────────────────────────
    schedule_odd_db = get_schedule("odd")
    schedule_even_db = get_schedule("even")

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
        count = len(schedule_for(d).get(DAYS[i], []))
        week_strip.append({
            "short": DAY_SHORT[i],
            "day": d.day,
            "count": min(count, 4),
            "link": f"?date={d.isoformat()}",
            "is_today": d == today,
            "is_selected": d == view_date,
            "label": f"{DAY_DISPLAY[DAYS[i]]}, {format_date_ro(d, long=True)}",
        })

    # ── template context ─────────────────────────────────────
    blocks_for_ui = prepare_blocks_for_ui(timeline, current_block)
    prev_date = view_date - timedelta(days=1)
    next_date = view_date + timedelta(days=1)

    context = {
        "user_name": USER_NAME,
        "state": state,
        "is_today": is_today,
        "has_courses": any(b["type"] == "course" for b in blocks_for_ui),
        "week_label": week_label,
        "week_parity": "odd" if week_is_odd else "even",
        "today_label": DAY_DISPLAY[DAYS[view_date.weekday()]],
        "view_date_str": format_date_ro(view_date, long=True),
        "view_date_iso": view_date.strftime("%Y-%m-%d"),
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
def manage():
    """Render the schedule management page."""
    week_key = "odd" if is_odd_week(ACADEMIC_WEEK1_START, datetime.now().date()) else "even"
    return render_template("manage.html", days=DAYS, day_display=DAY_DISPLAY, current_week=week_key)


# ═══════════════════════════════════════════════════ JSON API ══

@app.route("/api/activities", methods=["GET"])
def api_list_activities():
    """Return all activities as JSON."""
    return jsonify(get_all_activities())


@app.route("/api/activities", methods=["POST"])
def api_add_activity():
    """Add a new activity. Returns conflicts if any exist."""
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    day = data.get("day", "").strip()
    start_time = data.get("start_time", "").strip()
    end_time = data.get("end_time", "").strip()
    week_type = data.get("week_type", "both").strip()

    if not all([title, day, start_time, end_time]):
        return jsonify({"error": "Missing required fields"}), 400

    if day not in DAYS:
        return jsonify({"error": f"Invalid day: {day}"}), 400

    if end_time <= start_time:
        return jsonify({"error": "End time must be after start time"}), 400

    # Check conflicts
    conflicts = find_conflicts(day, start_time, end_time, week_type)
    if conflicts and not data.get("force"):
        return jsonify({"conflicts": conflicts, "needs_resolution": True}), 409

    new_id = add_activity(title, day, start_time, end_time, week_type)
    return jsonify({"id": new_id, "success": True}), 201


@app.route("/api/activities/<int:activity_id>", methods=["PUT"])
def api_update_activity(activity_id):
    """Update (move) an activity. Returns conflicts if any exist."""
    data = request.get_json(force=True)
    title = data.get("title")
    day = data.get("day")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    week_type = data.get("week_type")

    if day and day not in DAYS:
        return jsonify({"error": f"Invalid day: {day}"}), 400

    if start_time and end_time and end_time <= start_time:
        return jsonify({"error": "End time must be after start time"}), 400

    # We need the final values to check conflicts properly
    # Fetch current values and merge
    all_acts = get_all_activities()
    current = next((a for a in all_acts if a["id"] == activity_id), None)
    if current is None:
        return jsonify({"error": "Activity not found"}), 404

    final_day = day if day is not None else current["day"]
    final_start = start_time if start_time is not None else current["start_time"]
    final_end = end_time if end_time is not None else current["end_time"]
    final_wt = week_type if week_type is not None else current["week_type"]

    if final_end <= final_start:
        return jsonify({"error": "End time must be after start time"}), 400

    # Check conflicts (exclude self)
    conflicts = find_conflicts(final_day, final_start, final_end, final_wt, exclude_id=activity_id)
    if conflicts and not data.get("force"):
        return jsonify({"conflicts": conflicts, "needs_resolution": True}), 409

    update_activity(activity_id, title=title, day=day, start_time=start_time,
                    end_time=end_time, week_type=week_type)
    return jsonify({"success": True})


@app.route("/api/activities/<int:activity_id>", methods=["DELETE"])
def api_delete_activity(activity_id):
    """Delete an activity by id."""
    if delete_activity(activity_id):
        return jsonify({"success": True})
    return jsonify({"error": "Activity not found"}), 404


@app.route("/api/conflicts", methods=["POST"])
def api_check_conflicts():
    """Pre-check for conflicts without saving anything."""
    data = request.get_json(force=True)
    day = data.get("day", "")
    start_time = data.get("start_time", "")
    end_time = data.get("end_time", "")
    week_type = data.get("week_type", "both")
    exclude_id = data.get("exclude_id")

    conflicts = find_conflicts(day, start_time, end_time, week_type, exclude_id=exclude_id)
    return jsonify({"conflicts": conflicts})


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=APP_PORT)
