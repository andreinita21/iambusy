"""
Configuration — settings for IamBusy (multi-account edition).

``./setup.sh`` copies this file to ``schedule_config.py`` and fills in the
start date and port.  Schedules live in the database, one per account, and
are edited from the ``/manage`` page.
"""

from datetime import date

# ───────────── academic calendar (week-1 start) ──
# Default first day of week 1 for NEW accounts — every user can change
# their own value from the account page.
ACADEMIC_WEEK1_START: date = date(2026, 9, 28)

# ──────────────────────── application settings ──
APP_HOST: str = "0.0.0.0"  # "127.0.0.1" to listen on this machine only
APP_PORT: int = 2027
DEBUG: bool = False  # auto-reload + debugger; never enable on a reachable host

# ──────────────────────── accounts & sessions ──
# How long a device stays logged in when "keep me signed in" is ticked.
# The window slides: every visit extends it.
SESSION_DAYS: int = 30
# Lifetime of a session when "keep me signed in" is NOT ticked.
SHORT_SESSION_HOURS: int = 12

# True when visitors reach the app over HTTPS (Cloudflare tunnel / reverse
# proxy): cookies get the Secure flag and HSTS is sent.  Set it to False only
# for plain-HTTP use on a LAN — with True, browsers refuse to keep the login
# cookie over http://, so logging in only works through the HTTPS address.
COOKIE_SECURE: bool = True
# Networks the reverse proxy connects FROM.  Only requests coming from these
# addresses may tell us the real visitor IP (CF-Connecting-IP /
# X-Forwarded-For), which is what login rate limiting is keyed on.  Requests
# from anywhere else are identified by their own address, so the header
# cannot be spoofed from the LAN.  Defaults: loopback + Docker bridges
# (cloudflared running in a container).  Use [] when there is no proxy.
TRUSTED_PROXIES: list = ["127.0.0.0/8", "172.16.0.0/12"]
