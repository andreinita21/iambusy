"""
IamBusy — Flask application entry point.

Thin HTTP layer that delegates all schedule logic to :mod:`schedule_engine`
and reads configuration from :mod:`schedule_config`.
"""

from datetime import datetime, timedelta
from flask import Flask, render_template, request

from schedule_config import (
    ACADEMIC_WEEK1_START,
    APP_PORT,
    SCHEDULE_EVEN,
    SCHEDULE_ODD,
    USER_NAME,
)
from schedule_engine import (
    DAYS,
    build_day_timeline,
    compute_status,
    is_odd_week,
    prepare_blocks_for_ui,
)

app = Flask(__name__)


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

    # ── schedule selection ───────────────────────────────────
    week_is_odd = is_odd_week(ACADEMIC_WEEK1_START, view_date)
    schedule = SCHEDULE_ODD if week_is_odd else SCHEDULE_EVEN
    timeline = build_day_timeline(schedule, view_date)

    # ── status derivation ────────────────────────────────────
    if view_date == now.date():
        status_main, status_sub, current_block = compute_status(
            now, timeline, USER_NAME,
        )
    else:
        status_main = f"Program pentru {view_date.strftime('%d.%m.%Y')}"
        status_sub = ""
        current_block = None

    # ── template context ─────────────────────────────────────
    blocks_for_ui = prepare_blocks_for_ui(timeline, current_block)
    prev_date = view_date - timedelta(days=1)
    next_date = view_date + timedelta(days=1)

    context = {
        "week_label": "Săptămână impară" if week_is_odd else "Săptămână pară",
        "today_label": DAYS[view_date.weekday()],
        "view_date_str": view_date.strftime("%d %b %Y"),
        "view_date_iso": view_date.strftime("%Y-%m-%d"),
        "now": now.strftime("%H:%M"),
        "status_main": status_main,
        "status_sub": status_sub,
        "blocks": blocks_for_ui,
        "prev_link": f"?date={prev_date.strftime('%Y-%m-%d')}",
        "next_link": f"?date={next_date.strftime('%Y-%m-%d')}",
    }
    return render_template("index.html", context=context)


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=APP_PORT)
