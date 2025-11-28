from datetime import date

# User-configurable settings
USER_NAME = "Andrei"
ACADEMIC_WEEK1_START = date(2025, 10, 29)

# Weekly schedules
# Format per day: list of tuples: ("Course Title", "HH:MM", "HH:MM")
SCHEDULE_ODD = {
    'Luni': [
        ("AutoCAD Laborator", "16:00", "18:00"),
    ],
    'Marti': [
        ("Electrical Engineering Seminar", "12:00", "14:00"),
        ("Electrical Engineering Curs", "16:00", "18:00"),
        ("Operating Systems Curs", "19:00", "21:00"),
    ],
    'Miercuri': [
        ("Linear Algebra Seminar", "08:00", "10:00"),
        ("Programming Languages Laborator", "10:00", "12:00"),
    ],
    'Joi': [
        ("Linear Algebra Curs", "08:00", "10:00"),
        ("Calculus Curs", "10:00", "12:00"),
        ("Chemistry Curs", "12:00", "14:00"),
        ("Programming Languages", "16:00", "18:00"),
        ("Operating Systems Laborator", "18:00", "20:00"),
    ],
    'Vineri': [
        ("Engleza Seminar", "10:00", "12:00"),
        ("Calculus Seminar", "12:00", "14:00"),
    ],
    'Sambata': [],
    'Duminica': [],
}

SCHEDULE_EVEN = {
    'Luni': [
        ("Sport Laborator", "10:00", "12:00"),
        ("Chemistry Seminar", "12:00", "14:00"),
    ],
    'Marti': [
        ("Electrical Engineering Curs", "16:00", "18:00"),
        ("Operating Systems Curs", "19:00", "21:00"),
    ],
    'Miercuri': [
        ("Linear Algebra Seminar", "08:00", "10:00"),
        ("Programming Languages Laborator", "10:00", "12:00"),
    ],
    'Joi': [
        ("Linear Algebra Curs", "08:00", "10:00"),
        ("Calculus Curs", "10:00", "12:00"),
        ("AutoCAD", "14:00", "16:00"),
        ("Programming Languages", "16:00", "18:00"),
        ("Operating Systems Laborator", "18:00", "20:00"),
    ],
    'Vineri': [
        ("Engleza Seminar", "10:00", "12:00"),
        ("Calculus Seminar", "12:00", "14:00"),
    ],
    'Sambata': [],
    'Duminica': [],
}
