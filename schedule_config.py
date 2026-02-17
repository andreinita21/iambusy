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
ACADEMIC_WEEK1_START: date = date(2025, 10, 29)

# ──────────────────────── application settings ──
APP_PORT: int = 2026

# ──────────────────────── odd-week schedule ──
SCHEDULE_ODD: dict = {
    "Luni": [
        ("Calculus II (S) | AN204", "08:00", "10:00"),
        ("EE2 (S) | CB020", "10:00", "12:00"),
        ("API (C) | AN015", "14:00", "16:00"),
    ],
    "Marti": [
        ("Physics 1 (S) | AN202", "08:00", "10:00"),
        ("Prof Com (S) | CJ06", "10:00", "12:00"),
        ("Sport (L) | Sports Hall", "14:00", "16:00"),
    ],
    "Miercuri": [
        ("ED (C) | NaN", "08:00", "10:00"),
        ("EE2 (C) | CB105", "10:00", "12:00"),
        ("Physics 1 (L) | BN122A", "12:00", "14:00"),
    ],
    "Joi": [
        ("Physics 1 (C) | AN024", "10:00", "12:00"),
    ],
    "Vineri": [
        ("API (L) | JA001A", "08:00", "10:00"),
        ("Calculus II (C) | AN015", "10:00", "12:00"),
        ("DSA (C) | AN015", "12:00", "14:00"),
        ("DSA (L) | JA001A", "14:00", "16:00"),
    ],
    "Sambata": [],
    "Duminica": [],
}

# ──────────────────────── even-week schedule ──
SCHEDULE_EVEN: dict = {
    "Luni": [
        ("Calculus II (S) | AN204", "08:00", "10:00"),
        ("EE2 (S) | CB020", "10:00", "12:00"),
        ("API (C) | AN015", "14:00", "16:00"),
    ],
    "Marti": [
        ("Prof Com (S) | CJ06", "10:00", "12:00"),
    ],
    "Miercuri": [
        ("ED (C) | NaN", "08:00", "10:00"),
        ("EE2 (C) | CB105", "10:00", "12:00"),
        ("ED (S) | AN204", "12:00", "14:00"),
        ("ED (L) | CB020", "16:00", "18:00"),
    ],
    "Joi": [
        ("Physics 1 (C) | AN024", "10:00", "12:00"),
    ],
    "Vineri": [
        ("API (L) | JA001A", "08:00", "10:00"),
        ("Calculus II (C) | AN015", "10:00", "12:00"),
        ("DSA (C) | AN015", "12:00", "14:00"),
        ("DSA (L) | JA001A", "14:00", "16:00"),
    ],
    "Sambata": [],
    "Duminica": [],
}
