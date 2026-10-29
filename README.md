# IamBusy 📅

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.0+-000000?style=for-the-badge&logo=flask&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

**IamBusy** is a sleek, mobile-first web application designed to keep your university schedule organized and accessible. It automatically detects odd/even weeks, displays your daily timeline, and provides real-time status updates so you (and others) know exactly when you're free.

![Dashboard Preview](assets/screenshot.png)

> **Accounts edition** — a copy of IamBusy where every person has their own
> account and private schedule. Nothing is visible without logging in.

## 🔐 Accounts & security

-   **Invite-only sign-up**: creating an account needs an invite code generated in the admin panel (single- or multi-use, optional expiry, revocable).
-   **Stay signed in**: a device is remembered for 30 days after its last visit ("Ține-mă minte"); untick it on shared computers for a 12-hour session.
-   **Account page** (`/account`): display name, week-1 date, password change, and a list of connected devices you can log out one by one.
-   **Admin panel** (`/admin`): manage invite codes; disable / re-enable, log out, reset the password of, or delete accounts. The admin cannot read other people's schedules.

| Measure | How |
|---|---|
| Password storage | scrypt (salted, memory-hard); 10–128 characters, common passwords and passwords containing your name are refused |
| Session tokens | 256-bit random, `HttpOnly` + `SameSite=Lax` + `Secure` cookie; only the SHA-256 is stored, so a leaked database can't be replayed |
| Revocation | sessions live server-side: logout, password change, reset or disabling an account kills them immediately |
| CSRF | per-session token on every form and API call, plus a same-origin `Origin` check |
| Brute force | failed logins limited per IP+account (5 / 15 min), per account and per IP; invite-code guessing limited per IP |
| Enumeration | identical answer and timing for unknown user / wrong password / disabled account |
| Data isolation | every query is scoped to the logged-in user id |
| Browser hardening | strict Content-Security-Policy (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, HSTS, `Cache-Control: no-store` |
| On disk | `schedule.db`, `.secret_key` and `schedule_config.py` are mode `0600` and ignored by git |

```bash
python3 accounts.py create-admin              # first administrator (asks for the password)
python3 accounts.py reset-password USERNAME   # forgot-password rescue, run on the server
python3 accounts.py import-schedule USERNAME /path/to/old/schedule.db
python3 accounts.py list
```

## ✨ Features

-   **Smart Scheduling**: Automatically toggles between Odd and Even week schedules based on a configurable academic start date.
-   **Real-Time Status**: Instantly see if you are currently in a course or on a break, with a precise countdown to the next event.
-   **Live Clock**: A prominent clock that updates every second — no need to refresh.
-   **Day Navigation**: Switch days with **Previous/Next arrows**, the **week strip**, keyboard (`←` `→` `T` `C`) or swipe.
-   **Month Calendar with Busy Bars**: A custom date picker where every day shows a tiny 07:00–22:00 bar of its courses (colour-coded by type) and the academic week number. Tap a day to preview its full timeline and free windows *before* you commit to it, then open it or jump straight to "schedule something here".
-   **Free Windows at a Glance**: Each day lists its gaps between courses, and every gap has an **Adaugă** shortcut that opens the editor pre-filled with that day, week parity and time slot.
-   **Neobrutalist UI**: Thick borders, hard offset shadows, flat saturated colour and heavy type. Dark theme by default, light theme one click away (remembered per device).
-   **Mobile Optimized**: Bottom-sheet calendar, swipe navigation and a horizontally scrollable weekly grid.

### 🗓️ Schedule Management

A full-featured management page (`/manage`) lets you take control of your schedule:

-   **Weekly Grid View**: See all your activities laid out on a 7-day × 15-hour grid, with separate tabs for Odd and Even weeks.
-   **Add Activities**: Click any empty cell or use the **+** button to add a new activity with a name, day, time, and week type.
-   **Edit Activities**: Click on any existing activity card to edit its details.
-   **Drag & Drop**: Grab any activity card and drag it to a new time slot or day — duration is preserved automatically.
-   **Delete Activities**: Remove activities with a single click and confirmation dialog.
-   **Conflict Detection**: When adding or moving an activity into an occupied time slot, a warning dialog shows the conflicting activities and gives you two options:
    -   **Delete the conflicting activity** and proceed with the save.
    -   **Choose another time** and go back to the form.
-   **Smart Defaults**: Setting a start time automatically sets the end time to +2 hours.
-   **Persistent Storage**: All changes are saved to a local SQLite database (`schedule.db`).

## 🏗️ Architecture

```
iambusy/
├── app.py                    # Flask HTTP layer + REST API, security headers
├── auth.py                   # Login, registration, sessions, CSRF, rate limits, admin
├── accounts.py               # Command-line account maintenance
├── db.py                     # SQLite layer (users, sessions, invites, activities)
├── schedule_engine.py        # Business logic (week parity, timeline, status)
├── setup.sh                  # One-command install / config / systemd service
├── schedule_config.example.py # Blank config template
├── schedule_config.py        # Your settings (created by setup.sh, not in git)
├── static/
│   ├── style.css             # Neobrutalist design system (tokens, components)
│   ├── schedule.js           # Live clock, month calendar, navigation, theme
│   ├── manage.js             # Schedule management UI (grid, drag & drop, modals)
│   ├── auth.js               # Password reveal / strength meter, copy buttons
│   └── theme.js              # Applies the saved theme before first paint
├── templates/
│   ├── index.html            # Daily schedule view
│   ├── manage.html           # Schedule management page
│   ├── auth_base.html        # Shared layout of the account pages
│   ├── login.html / register.html
│   └── account.html / admin.html
├── .gitignore
├── requirements.txt
└── README.md
```

> **Note:** `schedule_config.py`, `schedule.db`, `.secret_key`, `.venv/`, `__pycache__/`, and `.DS_Store` are excluded from version control via `.gitignore`.

## 🚀 Getting Started

You need Python 3.9+ (on Debian/Ubuntu also `sudo apt install python3-venv`).

### Quick start

```bash
git clone https://github.com/andreinita21/iambusy.git
cd iambusy
./setup.sh
```

`setup.sh` asks for the first day of week 1 and a port, then:

1.  creates a virtualenv in `.venv/` and installs the dependencies,
2.  writes `schedule_config.py`,
3.  creates the SQLite database (`schedule.db`) and the `admin` account.

Start the app with the command it prints:

```bash
.venv/bin/gunicorn --bind 0.0.0.0:2027 app:app
```

Log in as `admin`, open **`/admin`**, generate an invite code and hand it to whoever should get an account. Each account builds its schedule from scratch in **`/manage`**.

### Hosting (keep it running)

```bash
./setup.sh --service
```

This asks for your sudo password and installs a systemd service (`iambusy-auth.service`) that starts at boot and restarts automatically if the app ever dies. If a unit with that name already exists, the script asks before replacing it and keeps a `.bak` copy.

```bash
systemctl status iambusy-auth        # is it running?
journalctl -u iambusy-auth -f        # live logs
sudo systemctl restart iambusy-auth  # after changing schedule_config.py or updating
```

The default configuration expects **HTTPS in front of the app** (Cloudflare tunnel or a reverse proxy): cookies are marked `Secure`, so logging in over plain `http://` does not work. For HTTP-only use on a LAN set `COOKIE_SECURE = False`.

### Setup options

| Option           | Description                                              |
|------------------|----------------------------------------------------------|
| `--start DATE`   | Default first day of week 1 for new accounts, `YYYY-MM-DD` |
| `--port PORT`    | Port to listen on (default: `2027`)                      |
| `--service`      | Install and start the systemd service (needs sudo)       |
| `-y`, `--yes`    | No questions, use defaults for anything not given        |

Re-running `./setup.sh` is safe: an existing `schedule_config.py` and `schedule.db` are never overwritten.

### Updating

```bash
git pull
./setup.sh                      # installs any new dependencies
sudo systemctl restart iambusy-auth  # if you use the service
```

### Development

Set `DEBUG = True` in `schedule_config.py` and run `.venv/bin/python app.py` for auto-reload and the Flask debugger. Never do this on a host other people can reach.

### API Endpoints

All endpoints need a logged-in session and only ever touch the caller's own schedule; `POST` / `PUT` / `DELETE` also need the `X-CSRF-Token` header (the value is in the page's `<meta name="csrf-token">`).

| Method   | Path                     | Description                        |
|----------|--------------------------|------------------------------------|
| `GET`    | `/api/activities`        | List all activities (JSON)         |
| `POST`   | `/api/activities`        | Add a new activity                 |
| `PUT`    | `/api/activities/<id>`   | Update / move an activity          |
| `DELETE` | `/api/activities/<id>`   | Delete an activity                 |
| `POST`   | `/api/conflicts`         | Check for time conflicts           |
| `GET`    | `/api/days?from=&to=`    | Per-day summaries (max 62 days) for the month calendar |

## 🛠️ Configuration

Everything lives in `schedule_config.py` (created by `setup.sh`, ignored by git):

| Setting                | Description                                                  |
|------------------------|--------------------------------------------------------------|
| `ACADEMIC_WEEK1_START` | Default first day of week 1 for new accounts (each user can change theirs) |
| `APP_HOST`, `APP_PORT` | Where the app listens                                        |
| `DEBUG`                | Flask auto-reload + debugger, for development only           |
| `SESSION_DAYS`         | How long a remembered device stays logged in (sliding, default 30) |
| `SHORT_SESSION_HOURS`  | Session length when "remember me" is unticked (default 12)   |
| `COOKIE_SECURE`        | `True` behind HTTPS (default); `False` only for plain-HTTP LAN use |
| `TRUSTED_PROXIES`      | Networks allowed to report the visitor IP (`CF-Connecting-IP`) |

Restart the app after editing it. Schedules are stored in `schedule.db` and edited through `/manage`.

## 📦 Tech Stack

-   **Backend**: Flask (Python), served by gunicorn
-   **Database**: SQLite (via `db.py`)
-   **Frontend**: HTML5, CSS3 (Custom Design System), JavaScript
-   **Templating**: Jinja2

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
