"""
Schedule Configuration — settings for IamBusy.

``./setup.sh`` copies this file to ``schedule_config.py`` and fills in your
name, start date and port.  The schedule starts out blank: add your
activities from the ``/manage`` page.
"""

from datetime import date

# ─────────────────────────────── user identity ──
# Display name used in the UI
USER_NAME: str = "Your Name"

# ───────────── academic calendar (week-1 start) ──
# First day of week 1 — used to compute odd/even weeks
ACADEMIC_WEEK1_START: date = date(2026, 9, 28)

# ──────────────────────── application settings ──
APP_HOST: str = "0.0.0.0"  # "127.0.0.1" to listen on this machine only
APP_PORT: int = 2026
DEBUG: bool = False  # auto-reload + debugger; never enable on a public host

# ──────────────────────── initial schedule (optional) ──
# Only used to seed an empty database (or with ``python3 db.py --reseed``).
# Leave blank to start from scratch and build the schedule in /manage.
# Days must be one of: 'Luni','Marti','Miercuri','Joi','Vineri','Sambata','Duminica'
# Each entry is a tuple: ("Subject (Type) | Room", "HH:MM", "HH:MM")
SCHEDULE_ODD: dict = {
    "Luni": [],
    "Marti": [],
    "Miercuri": [],
    "Joi": [],
    "Vineri": [],
    "Sambata": [],
    "Duminica": [],
}

SCHEDULE_EVEN: dict = {
    "Luni": [],
    "Marti": [],
    "Miercuri": [],
    "Joi": [],
    "Vineri": [],
    "Sambata": [],
    "Duminica": [],
}
