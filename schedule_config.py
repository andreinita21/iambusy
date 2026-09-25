"""
Schedule Configuration — User-editable settings for IamBusy.

Rename ``schedule_config.example.py`` to ``schedule_config.py`` and
fill in your own data.  The format for each day is::

    "DayName": [
        ("Subject (Type) | Room", "HH:MM", "HH:MM"),
    ]
"""

from datetime import date

# ─────────────────────────────── user identity ──
USER_NAME: str = "Andrei"

# ───────────── academic calendar (week-1 start) ──
ACADEMIC_WEEK1_START: date = date(2026, 9, 28)

# ──────────────────────── application settings ──
APP_PORT: int = 2026

# ──────────────────────── odd-week schedule ──
SCHEDULE_ODD: dict = {
    "Luni": [
        ("Fundamental Electronic Circuits (C) | Leu B110", "14:00", "16:00"),
        ("Microeconomie (S) | CJ106", "18:00", "20:00"),
    ],
    "Marti": [
        ("USO ACS (C) | EC105", "10:00", "12:00"),
        ("Databases (L) | CJ201", "12:00", "14:00"),
    ],
    "Miercuri": [
        ("Special Mathematics (C) | AN015", "08:00", "10:00"),
        ("Statistics (C) | AN015", "10:00", "12:00"),
        ("Microeconomics (C) | AN024", "12:00", "14:00"),
    ],
    "Joi": [
        ("Physics (C) | AN024", "08:00", "10:00"),
        ("Special Mathematics (S) | CB020", "14:00", "16:00"),
        ("OOP (L) | CJ101", "18:00", "20:00"),
    ],
    "Vineri": [
        ("Databases (C) | AN017", "10:00", "12:00"),
        ("OOP (C) | AN017", "12:00", "14:00"),
    ],
    "Sambata": [],
    "Duminica": [],
}

# ──────────────────────── even-week schedule ──
SCHEDULE_EVEN: dict = {
    "Luni": [
        ("Fundamental Electronic Circuits (C) | Leu B110", "14:00", "16:00"),
    ],
    "Marti": [
        ("Fundamental Electronic Circuits (L) | CB105", "08:00", "10:00"),
        ("Statistics (S) | AN204", "10:00", "12:00"),
        ("Physics (L) | BN030", "12:00", "14:00"),
    ],
    "Miercuri": [
        ("Special Mathematics (C) | AN015", "08:00", "10:00"),
        ("Statistics (C) | AN015", "10:00", "12:00"),
    ],
    "Joi": [
        ("Physics (C) | AN024", "08:00", "10:00"),
        ("Special Mathematics (S) | CB020", "14:00", "16:00"),
        ("OOP (L) | CJ101", "18:00", "20:00"),
    ],
    "Vineri": [
        ("Databases (C) | AN017", "10:00", "12:00"),
        ("OOP (C) | AN017", "12:00", "14:00"),
        ("Fundamental Electronic Circuits (L) | Leu A412", "18:00", "20:00"),
    ],
    "Sambata": [],
    "Duminica": [],
}
