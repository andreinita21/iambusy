from datetime import date

# Rename this file to schedule_config.py and fill in your details

# Display name used in UI
USER_NAME = "Your Name"

# First academic week start date (used to compute odd/even weeks)
ACADEMIC_WEEK1_START = date(2025, 10, 29)

# Define weekly schedules for odd and even weeks
# Days must be one of: 'Luni','Marti','Miercuri','Joi','Vineri','Sambata','Duminica'
# Each entry is a list of tuples: ("Course Title", "HH:MM", "HH:MM")
SCHEDULE_ODD = {
    'Luni': [],
    'Marti': [],
    'Miercuri': [],
    'Joi': [],
    'Vineri': [],
    'Sambata': [],
    'Duminica': [],
}

SCHEDULE_EVEN = {
    'Luni': [],
    'Marti': [],
    'Miercuri': [],
    'Joi': [],
    'Vineri': [],
    'Sambata': [],
    'Duminica': [],
}
