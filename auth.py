"""
Accounts for IamBusy — login, registration, device sessions, admin panel.

Security model
--------------
* Passwords are hashed with scrypt (salted, memory-hard); never stored or
  logged in clear text.
* A login creates a random 256-bit token.  The browser keeps it in an
  ``HttpOnly`` + ``SameSite=Lax`` cookie; the database keeps only its
  SHA-256, so a leaked database cannot be replayed as a session.
* Sessions are server-side: they can be listed and revoked per device, and
  are dropped when the password changes or the account is disabled.
* Every state-changing request needs a CSRF token and a same-origin
  ``Origin`` header.
* Failed logins / registrations are rate limited per IP and per account.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import date, datetime
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import captcha
import db
import db_social
import mailer

bp = Blueprint("auth", __name__)

SESSION_COOKIE = "iambusy_auth"
# Marks a browser that has logged in to an account before.  The per-account
# login limit (which anyone could trip on purpose to lock the owner out) does
# not apply to it; the per-IP limits still do.
DEVICE_COOKIE = "iambusy_device"
DEVICE_COOKIE_DAYS = 365
HASH_METHOD = "scrypt:32768:8:1"

USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])$")
PASSWORD_MIN = 10
PASSWORD_MAX = 128  # bounds the work an attacker can force on the hasher
DISPLAY_NAME_MAX = 40
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L

# Rate limits: (max failures, window in seconds)
LIMIT_LOGIN_PAIR = (5, 15 * 60)     # same IP hammering the same account
LIMIT_LOGIN_USER = (25, 60 * 60)    # one account attacked from many IPs
LIMIT_DELETE = (5, 15 * 60)         # wrong passwords on "delete my account"
LIMIT_LOGIN_IP = (30, 15 * 60)      # one IP spraying many accounts
LIMIT_REGISTER_IP = (8, 60 * 60)    # sign-ups and invite-code guesses per IP
MAX_FRIEND_SIGNUPS_PER_DAY = 10     # new accounts through one person's friend links

COMMON_PASSWORDS = frozenset({
    "1234567890", "12345678910", "123456789a", "qwertyuiop", "qwerty12345",
    "password12", "password123", "password1234", "parola1234", "parola12345",
    "iloveyou12", "1q2w3e4r5t", "1q2w3e4r5t6y", "qwerty123456", "abcdefghij",
    "0123456789", "0987654321", "1122334455", "letmein1234", "welcome1234",
    "administrator", "adminadmin", "admin12345", "iambusy1234", "qazwsxedcr",
})

# Verified against when the username doesn't exist, so that a wrong username
# costs the same time as a wrong password.
_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(16), method=HASH_METHOD)

_settings = {
    "session_days": 30,
    "short_session_hours": 12,
    "cookie_secure": False,
    "trusted_proxies": [],
    "default_week1_start": date.today(),
    "site_url": "",
}


def configure(*, session_days, short_session_hours, cookie_secure, trusted_proxies,
              default_week1_start, site_url=""):
    _settings.update(
        site_url=(site_url or "").strip().rstrip("/"),
        trusted_proxies=[ipaddress.ip_network(n) for n in trusted_proxies],
        session_days=max(1, int(session_days)),
        short_session_hours=max(1, int(short_session_hours)),
        cookie_secure=bool(cookie_secure),
        default_week1_start=default_week1_start,
    )


# ═════════════════════════════════════════════════ Secret key ══

def load_secret_key(app_dir: str) -> bytes:
    """Read the cookie-signing key, creating it (mode 0600) on first run."""
    path = os.path.join(app_dir, ".secret_key")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as fh:
            fh.write(secrets.token_bytes(48))
    for _ in range(20):  # another worker may still be writing it
        with open(path, "rb") as fh:
            key = fh.read()
        if len(key) >= 32:
            return key
        time.sleep(0.05)
    raise SystemExit(f"{path} is empty or too short — delete it and restart.")


# ═════════════════════════════════════════════════ Passwords ══

def hash_password(password: str) -> str:
    return generate_password_hash(password, method=HASH_METHOD)


def verify_password(password_hash: str, password: str) -> bool:
    if len(password) > PASSWORD_MAX:
        return False
    return check_password_hash(password_hash, password)


def normalize_username(raw: str) -> str:
    return (raw or "").strip().lower()


def validate_username(username: str):
    if not USERNAME_RE.match(username):
        return ("Numele de utilizator are 3–32 de caractere: litere mici, cifre, "
                "punct, liniuță sau underscore (începe și se termină cu literă/cifră).")
    return None


def validate_password(password: str, username: str = "", display_name: str = ""):
    if len(password) < PASSWORD_MIN:
        return f"Parola trebuie să aibă cel puțin {PASSWORD_MIN} caractere."
    if len(password) > PASSWORD_MAX:
        return f"Parola poate avea cel mult {PASSWORD_MAX} de caractere."
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(password)) < 4:
        return "Parola este prea ușor de ghicit. Alege alta."
    for own in (username, display_name.strip().lower()):
        if own and len(own) >= 3 and own in lowered:
            return "Parola nu trebuie să conțină numele tău."
    return None


def validate_display_name(name: str):
    if not name:
        return "Spune-ne cum să te afișăm."
    if len(name) > DISPLAY_NAME_MAX:
        return f"Numele afișat poate avea cel mult {DISPLAY_NAME_MAX} de caractere."
    if any(ord(ch) < 32 or ch in "<>" for ch in name):
        return "Numele afișat conține caractere nepermise."
    return None


def parse_week1_start(raw: str):
    try:
        d = date.fromisoformat((raw or "").strip())
    except ValueError:
        return None
    return d if 2000 <= d.year <= 2100 else None


def new_invite_code() -> str:
    groups = ["".join(secrets.choice(INVITE_ALPHABET) for _ in range(4)) for _ in range(3)]
    return "-".join(groups)


def normalize_invite(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").upper()[:64]


# ═══════════════════════════════════════════ Device sessions ══

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _lifetime(persistent: bool) -> int:
    if persistent:
        return _settings["session_days"] * 86400
    return _settings["short_session_hours"] * 3600


def _parse_ip(raw):
    try:
        return ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return None


def from_trusted_proxy() -> bool:
    """Did this request come through our reverse proxy (so its headers count)?"""
    peer = _parse_ip(request.remote_addr)
    return peer is not None and any(peer in net for net in _settings["trusted_proxies"])


def client_ip() -> str:
    """Visitor address — forwarded headers count only from a trusted proxy."""
    peer = _parse_ip(request.remote_addr)
    if peer is None:
        return (request.remote_addr or "")[:64]
    if from_trusted_proxy():
        forwarded = _parse_ip(request.headers.get("CF-Connecting-IP")) or _parse_ip(
            (request.headers.get("X-Forwarded-For") or "").split(",")[-1])
        if forwarded is not None:
            return str(forwarded)
    return str(peer)


def start_session(user_id: int, persistent: bool) -> None:
    """Create a device session; the cookie is attached in ``after_request``."""
    token = secrets.token_urlsafe(32)
    db.create_session(
        user_id=user_id,
        token_hash=_hash_token(token),
        expires_at=int(time.time()) + _lifetime(persistent),
        persistent=persistent,
        user_agent=(request.headers.get("User-Agent") or "")[:200],
        ip=client_ip(),
    )
    db.update_user(user_id, last_login_at=int(time.time()))
    # Fresh CSRF token for the authenticated session (no fixation).
    session.clear()
    session.permanent = True
    session["csrf"] = secrets.token_urlsafe(32)
    g.set_auth_cookie = (token, persistent)


def end_session() -> None:
    if g.get("user"):
        db.delete_session(g.user["session_id"], g.user["id"])
    session.clear()
    g.user = None
    g.clear_auth_cookie = True


def load_current_user() -> None:
    """``before_request`` hook: resolve the auth cookie into ``g.user``."""
    g.user = None
    if request.endpoint == "static":
        return
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        return
    row = db.get_session(_hash_token(token))
    if row is None or not row["is_active"]:
        g.clear_auth_cookie = True
        return
    g.user = row
    # Sliding expiry — refreshed at most once an hour to keep writes rare.
    now = int(time.time())
    if now - row["last_seen"] > 3600:
        persistent = bool(row["persistent"])
        db.touch_session(row["session_id"], now + _lifetime(persistent), client_ip())
        g.set_auth_cookie = (token, persistent)


def _device_mark(username: str) -> str:
    key = current_app.secret_key
    return hmac.new(key if isinstance(key, bytes) else key.encode(),
                    f"device:{username}".encode(), hashlib.sha256).hexdigest()[:32]


def known_device(username: str) -> bool:
    sent = request.cookies.get(DEVICE_COOKIE, "")
    return any(hmac.compare_digest(mark, _device_mark(username)) for mark in sent.split(".")[:5])


def remember_device(username: str) -> None:
    marks = [m for m in request.cookies.get(DEVICE_COOKIE, "").split(".")[:5] if m]
    mark = _device_mark(username)
    g.set_device_cookie = ".".join([mark] + [m for m in marks if m != mark][:4])


def apply_auth_cookie(response):
    """``after_request`` hook: set / refresh / clear the auth cookie."""
    device = g.pop("set_device_cookie", None)
    if device:
        response.set_cookie(DEVICE_COOKIE, device, max_age=DEVICE_COOKIE_DAYS * 86400,
                            httponly=True, samesite="Lax", secure=_settings["cookie_secure"],
                            path="/")
    pending = g.pop("set_auth_cookie", None)
    if pending:
        token, persistent = pending
        response.set_cookie(
            SESSION_COOKIE, token,
            max_age=_lifetime(True) if persistent else None,
            httponly=True, samesite="Lax", secure=_settings["cookie_secure"], path="/",
        )
    elif g.pop("clear_auth_cookie", False):
        response.delete_cookie(
            SESSION_COOKIE, path="/", httponly=True, samesite="Lax",
            secure=_settings["cookie_secure"],
        )
    return response


# ═══════════════════════════════════════════════════════ CSRF ══

# Authorised by a signed token in the URL instead (mail clients post the RFC 8058
# one-click unsubscribe without any page or cookie).
CSRF_EXEMPT = {"social.unsubscribe"}


def csrf_token() -> str:
    if "csrf" not in session:
        session.permanent = True
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _same_origin() -> bool:
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # non-browser clients and old browsers don't send it
    if origin == "null":
        return False
    return urlsplit(origin).netloc == request.host


def csrf_protect():
    """``before_request`` hook: reject cross-site state-changing requests."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    if request.endpoint in CSRF_EXEMPT:
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf", "")
    if not expected or not _same_origin() or not hmac.compare_digest(sent, expected):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Sesiune expirată — reîncarcă pagina.", "code": "csrf"}), 403
        flash("Formularul a expirat. Încearcă din nou.", "error")
        # Pages reachable without an account go back to themselves.
        if request.endpoint in ("auth.login", "auth.register", "social.friend_link",
                                "recovery.forgot", "recovery.reset_password"):
            return redirect(request.path)
        if request.endpoint == "sharing.share_pin":
            return redirect(request.path.rsplit("/", 1)[0])
        return redirect(url_for("index"))
    return None


# ══════════════════════════════════════════════════ Decorators ══

def absolute_url(path: str) -> str:
    """Full link to *path* for copying / sharing — on the public address when set."""
    if _settings["site_url"]:
        return _settings["site_url"] + path
    scheme = "https" if _settings["cookie_secure"] else request.scheme
    return f"{scheme}://{request.host}{path}"


def safe_next(target) -> str:
    """Only allow redirects to local paths (no open redirect)."""
    if (not target or not target.startswith("/") or target.startswith("//")
            or "\\" in target or any(ord(c) < 32 for c in target)):
        return "/"
    return target


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Trebuie să te autentifici.", "code": "auth"}), 401
            next_url = request.full_path if request.query_string else request.path
            return redirect(url_for("auth.login", next=next_url if next_url != "/" else None))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(404)  # don't advertise that the panel exists
        return view(*args, **kwargs)
    return wrapped


def _limited(key: str, limit) -> bool:
    max_failures, window = limit
    return db.count_attempts(key, window) >= max_failures


# ═════════════════════════════════════════════ Login / logout ══

@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(safe_next(request.args.get("next")))

    next_url = safe_next(request.values.get("next"))
    username = ""
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))[:64]
        password = request.form.get("password") or ""
        remember = request.form.get("remember") == "1"

        ip = client_ip()
        keys = {"pair": f"login:{ip}:{username}", "user": f"login-user:{username}",
                "ip": f"login-ip:{ip}"}
        if (_limited(keys["pair"], LIMIT_LOGIN_PAIR) or _limited(keys["ip"], LIMIT_LOGIN_IP)
                or (_limited(keys["user"], LIMIT_LOGIN_USER) and not known_device(username))):
            flash("Prea multe încercări. Așteaptă câteva minute și încearcă din nou.", "error")
            return render_template("login.html", username=username, next_url=next_url), 429

        user = db.get_user_by_username(username) if username else None
        ok = verify_password(user["password_hash"] if user else _DUMMY_HASH, password)
        if not (user and ok and user["is_active"]):
            db.record_attempt(*keys.values())
            # Same message whether the account exists, is disabled or the
            # password is wrong — nothing to enumerate.
            flash("Nume de utilizator sau parolă greșită.", "error")
            return render_template("login.html", username=username, next_url=next_url), 401

        db.clear_attempts(keys["pair"], keys["user"])
        start_session(user["id"], persistent=remember)
        remember_device(user["username"])
        return redirect(next_url)

    return render_template("login.html", username=username, next_url=next_url)


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
EMAIL_MAX = 254


def normalize_email(raw) -> str:
    return (raw or "").strip().lower()[:EMAIL_MAX + 1]


def valid_email(email: str) -> bool:
    return len(email) <= EMAIL_MAX and bool(EMAIL_RE.match(email))


FRIEND_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def normalize_friend_code(raw: str) -> str:
    code = (raw or "").strip()
    return code if FRIEND_CODE_RE.match(code) else ""


def signup_open() -> bool:
    """Admin switch: can anyone create an account, or only with an invite?"""
    return db.get_setting("open_signup", "1") == "1"


def friend_signup_enabled() -> bool:
    """Admin switch: may friend links create accounts (bypassing invite codes)?"""
    return db.get_setting("friend_link_signup", "1") == "1"


@bp.route("/register", methods=["GET", "POST"])
def register():
    if g.user is not None:
        return redirect(url_for("index"))

    open_signup = signup_open()
    form = {"username": "", "display_name": "", "email": "", "invite": "", "from_link": False,
            "friend": "", "friend_owner": None, "open": open_signup}
    ip_key = f"register-ip:{client_ip()}"
    friend_code = normalize_friend_code(request.values.get("friend"))
    if friend_code:
        # Arriving through someone's friend link: it replaces the invite
        # code when the administrator allows that.
        if _limited(ip_key, LIMIT_REGISTER_IP):
            flash("Prea multe încercări. Încearcă din nou peste o oră.", "error")
            return render_template("register.html", form=form), 429
        link = db_social.get_usable_friend_link(friend_code) if friend_signup_enabled() else None
        if link is not None and db_social.count_invited_since(
                link["user_id"], int(time.time()) - 86400) >= MAX_FRIEND_SIGNUPS_PER_DAY:
            link = None
        if link is None and open_signup:
            flash("Linkul de prietenie nu mai este valid — îți poți face cont, dar nu veți deveni "
                  "automat prieteni.", "error")
        elif link is None:
            db.record_attempt(ip_key)
            flash("Linkul de prietenie nu mai este valid sau nu poate crea conturi. "
                  "Cere un cod de invitație.", "error")
        else:
            form["friend"] = friend_code
            form["friend_owner"] = {"display_name": link["display_name"],
                                    "username": link["username"]}
    elif request.method == "GET" and request.args.get("code") and not open_signup:
        # Arriving through an invite link: check the code right away so the
        # visitor doesn't fill in the whole form for nothing.  Wrong codes
        # count against the same limit as failed registrations.
        code = normalize_invite(request.args.get("code"))
        if _limited(ip_key, LIMIT_REGISTER_IP):
            flash("Prea multe încercări. Încearcă din nou peste o oră.", "error")
            return render_template("register.html", form=form), 429
        if db.invite_is_usable(code):
            form["invite"] = code
            form["from_link"] = True
        else:
            db.record_attempt(ip_key)
            flash("Linkul de invitație nu mai este valid. Cere unul nou.", "error")
    if request.method == "POST":
        form["from_link"] = request.form.get("from_link") == "1"
        form["username"] = normalize_username(request.form.get("username"))[:64]
        form["display_name"] = " ".join((request.form.get("display_name") or "").split())[:80]
        form["email"] = normalize_email(request.form.get("email"))
        form["invite"] = "" if form["friend"] or open_signup else normalize_invite(
            request.form.get("invite"))
        password = request.form.get("password") or ""
        confirm = request.form.get("password_confirm") or ""

        if _limited(ip_key, LIMIT_REGISTER_IP):
            flash("Prea multe încercări. Încearcă din nou peste o oră.", "error")
            return render_template("register.html", form=form), 429

        error = (
            validate_username(form["username"])
            or validate_display_name(form["display_name"])
            or (None if valid_email(form["email"]) else "Adresa de email nu pare validă.")
            or validate_password(password, form["username"], form["display_name"])
            or (None if hmac.compare_digest(password, confirm) else "Parolele nu coincid.")
            or (None if form["invite"] or form["friend"] or open_signup
                else "Ai nevoie de un cod de invitație.")
            or (None if captcha.verify(request.form.get("cf-turnstile-response"), client_ip())
                else "Confirmă că nu ești robot (bifa de deasupra butonului).")
        )
        if error is None and db.get_user_by_username(form["username"]):
            error = "Numele de utilizator este deja folosit."
        if error:
            flash(error, "error")
            return render_template("register.html", form=form), 400

        try:
            user_id = db.create_user(
                username=form["username"],
                display_name=form["display_name"],
                password_hash=hash_password(password),
                week1_start=_settings["default_week1_start"].isoformat(),
                invite_code=form["invite"] or None,
                friend_code=form["friend"] or None,
            )
        except sqlite3.IntegrityError:
            flash("Numele de utilizator este deja folosit.", "error")
            return render_template("register.html", form=form), 400

        if user_id is None:
            db.record_attempt(ip_key)
            flash("Codul de invitație nu este valid sau a expirat.", "error")
            return render_template("register.html", form=form), 400

        # Successful sign-ups count too: one visitor can't mint accounts in bulk.
        db.record_attempt(ip_key)
        start_session(user_id, persistent=True)
        remember_device(form["username"])
        # The account is usable once the address is confirmed (recovery.require_email).
        from recovery import send_verification  # recovery imports this module
        send_verification(db.get_user(user_id), form["email"])
        if form["friend"]:
            flash(f"Cont creat. Tu și {form['friend_owner']['display_name']} sunteți acum "
                  "prieteni.", "ok")
        else:
            flash("Cont creat. Bine ai venit!", "ok")
        return redirect(url_for("recovery.email_required"))

    return render_template("register.html", form=form)


@bp.route("/logout", methods=["POST"])
def logout():
    end_session()
    flash("Te-ai deconectat de pe acest dispozitiv.", "ok")
    return redirect(url_for("auth.login"))


# ═════════════════════════════════════════════════════ Account ══

def _describe_device(user_agent: str) -> str:
    ua = user_agent or ""
    if "iPhone" in ua or "iPad" in ua:
        system = "iOS"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "macOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "Dispozitiv necunoscut"
    for marker, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                         ("Chrome/", "Chrome"), ("Safari/", "Safari")):
        if marker in ua:
            return f"{name} · {system}"
    return system


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def _account_context():
    sessions = db.list_sessions(g.user["id"])
    for s in sessions:
        s["device"] = _describe_device(s["user_agent"])
        s["is_current"] = s["id"] == g.user["session_id"]
        s["last_seen_str"] = _fmt_ts(s["last_seen"])
        s["created_str"] = _fmt_ts(s["created_at"])
    return {"user": g.user, "sessions": sessions, "session_days": _settings["session_days"],
            "mail_enabled": mailer.enabled(), "pending_email": db.pending_email(g.user["id"]),
            "notify_kinds": list(db.EMAIL_KIND_LABELS.items()),
            "notify_on": (g.user["email_notify"] or "").split(",")}


@bp.route("/account")
@login_required
def account():
    return render_template("account.html", **_account_context())


@bp.route("/account/profile", methods=["POST"])
@login_required
def account_profile():
    display_name = " ".join((request.form.get("display_name") or "").split())[:80]
    week1 = parse_week1_start(request.form.get("week1_start"))
    error = validate_display_name(display_name)
    if error is None and week1 is None:
        error = "Data de început a săptămânii 1 nu este validă."
    if error:
        flash(error, "error")
    else:
        db.update_user(g.user["id"], display_name=display_name, week1_start=week1.isoformat())
        flash("Profil salvat.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/account/password", methods=["POST"])
@login_required
def account_password():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("new_password_confirm") or ""

    key = f"pwchange:{g.user['id']}"
    if _limited(key, LIMIT_LOGIN_PAIR):
        flash("Prea multe încercări. Așteaptă câteva minute.", "error")
        return redirect(url_for("auth.account"))
    if not verify_password(g.user["password_hash"], current):
        db.record_attempt(key)
        flash("Parola actuală nu este corectă.", "error")
        return redirect(url_for("auth.account"))

    error = validate_password(new, g.user["username"], g.user["display_name"])
    if error is None and not hmac.compare_digest(new, confirm):
        error = "Parolele nu coincid."
    if error is None and hmac.compare_digest(new, current):
        error = "Parola nouă trebuie să fie diferită de cea veche."
    if error:
        flash(error, "error")
        return redirect(url_for("auth.account"))

    db.update_user(g.user["id"], password_hash=hash_password(new))
    db.drop_email_tokens(g.user["id"], "reset")
    dropped = db.delete_user_sessions(g.user["id"], except_session_id=g.user["session_id"])
    flash(f"Parolă schimbată. {dropped} alte dispozitive au fost deconectate.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/account/sessions/<int:session_id>/revoke", methods=["POST"])
@login_required
def account_revoke_session(session_id):
    if session_id == g.user["session_id"]:
        end_session()
        return redirect(url_for("auth.login"))
    if db.delete_session(session_id, g.user["id"]):
        flash("Dispozitiv deconectat.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/account/sessions/revoke-others", methods=["POST"])
@login_required
def account_revoke_others():
    dropped = db.delete_user_sessions(g.user["id"], except_session_id=g.user["session_id"])
    flash(f"{dropped} alte dispozitive au fost deconectate.", "ok")
    return redirect(url_for("auth.account"))


@bp.route("/account/export")
@login_required
def account_export():
    """Download everything stored about this account as JSON (GDPR art. 15/20)."""
    data = db_social.export_user_data(g.user["id"])
    data = {"exported_at": datetime.now().isoformat(timespec="seconds"), **data}
    filename = f"iambusy-{g.user['username']}-{date.today().isoformat()}.json"
    return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    """Delete my own account and everything in it, after re-entering the password."""
    user = db.get_user(g.user["id"])
    password = request.form.get("password") or ""
    key = f"delete:{user['id']}"
    if _limited(key, LIMIT_DELETE):
        flash("Prea multe încercări. Așteaptă câteva minute.", "error")
    elif not verify_password(user["password_hash"], password):
        db.record_attempt(key)
        flash("Parola nu e corectă — contul nu a fost șters.", "error")
    elif user["is_admin"] and not any(
            u["is_admin"] and u["is_active"] and u["id"] != user["id"] for u in db.list_users()):
        flash("Ești singurul administrator. Fă pe altcineva administrator înainte să-ți "
              "ștergi contul.", "error")
    else:
        end_session()
        db.delete_user(user["id"])
        flash("Contul tău și toate datele lui au fost șterse definitiv.", "ok")
        return redirect("/")
    return redirect(url_for("auth.account") + "#delete")


# ═══════════════════════════════════════════════ Admin panel ══

def _render_admin(temp_password=None):
    users = db.list_users()
    for u in users:
        u["created_str"] = _fmt_ts(u["created_at"])
        u["last_login_str"] = _fmt_ts(u["last_login_at"])
    invites = db.list_invites()
    for inv in invites:
        inv["link"] = absolute_url(f"/register?code={inv['code']}")
        inv["created_str"] = _fmt_ts(inv["created_at"])
        inv["expires_str"] = _fmt_ts(inv["expires_at"]) if inv["expires_at"] else "nu expiră"
    return render_template(
        "admin.html", user=g.user, users=users, invites=invites,
        temp_password=temp_password, friend_signup=friend_signup_enabled(),
        open_signup=signup_open(),
    )


@bp.route("/admin")
@admin_required
def admin():
    return _render_admin()


@bp.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    open_signup = request.form.get("open_signup") == "1"
    friend_links = request.form.get("friend_link_signup") == "1"
    db.set_setting("open_signup", "1" if open_signup else "0")
    db.set_setting("friend_link_signup", "1" if friend_links else "0")
    flash("Înscriere " + ("deschisă oricui" if open_signup else "doar cu invitație") + "; linkurile de "
          "prietenie " + ("pot" if friend_links else "nu pot") + " crea conturi.", "ok")
    return redirect(url_for("auth.admin") + "#settings")


@bp.route("/admin/invites", methods=["POST"])
@admin_required
def admin_create_invite():
    note = " ".join((request.form.get("note") or "").split())[:60]
    try:
        max_uses = int(request.form.get("max_uses") or 1)
        days = int(request.form.get("days") or 0)
    except ValueError:
        max_uses, days = 0, -1
    if not 1 <= max_uses <= 100 or not 0 <= days <= 365:
        flash("Valori nevalide: 1–100 utilizări, 0–365 zile.", "error")
        return redirect(url_for("auth.admin"))
    expires_at = int(time.time()) + days * 86400 if days else None
    code = new_invite_code()
    db.create_invite(code, note, max_uses, expires_at)
    flash("Invitație creată — copiază linkul din listă și trimite-l.", "ok")
    return redirect(url_for("auth.admin") + "#invites")


@bp.route("/admin/invites/<int:invite_id>/revoke", methods=["POST"])
@admin_required
def admin_revoke_invite(invite_id):
    if db.revoke_invite(invite_id):
        flash("Cod revocat.", "ok")
    return redirect(url_for("auth.admin") + "#invites")


@bp.route("/admin/invites/<int:invite_id>/delete", methods=["POST"])
@admin_required
def admin_delete_invite(invite_id):
    if db.delete_invite(invite_id):
        flash("Cod șters.", "ok")
    return redirect(url_for("auth.admin") + "#invites")


def _admin_target(user_id: int) -> dict:
    target = db.get_user(user_id)
    if target is None:
        abort(404)
    return target


@bp.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def admin_toggle_user(user_id):
    target = _admin_target(user_id)
    if target["id"] == g.user["id"]:
        flash("Nu îți poți dezactiva propriul cont.", "error")
    elif target["is_active"]:
        db.update_user(target["id"], is_active=0)
        db.delete_user_sessions(target["id"])
        flash(f"Contul {target['username']} a fost dezactivat și deconectat.", "ok")
    else:
        db.update_user(target["id"], is_active=1)
        flash(f"Contul {target['username']} a fost reactivat.", "ok")
    return redirect(url_for("auth.admin"))


@bp.route("/admin/users/<int:user_id>/logout", methods=["POST"])
@admin_required
def admin_logout_user(user_id):
    target = _admin_target(user_id)
    if target["id"] == g.user["id"]:
        dropped = db.delete_user_sessions(target["id"], except_session_id=g.user["session_id"])
    else:
        dropped = db.delete_user_sessions(target["id"])
    flash(f"{target['username']}: {dropped} dispozitive deconectate.", "ok")
    return redirect(url_for("auth.admin"))


@bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    target = _admin_target(user_id)
    if target["id"] == g.user["id"]:
        flash("Schimbă-ți propria parolă din pagina Contul meu.", "error")
        return redirect(url_for("auth.admin"))
    temp = "-".join(secrets.token_urlsafe(4) for _ in range(3))
    db.update_user(target["id"], password_hash=hash_password(temp))
    db.drop_email_tokens(target["id"], "reset")
    db.delete_user_sessions(target["id"])
    db.clear_attempts(f"login-user:{target['username']}")
    # Rendered straight into this response and never stored: the temporary
    # password is visible exactly once.
    return _render_admin(temp_password={"username": target["username"], "password": temp})


@bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    target = _admin_target(user_id)
    if target["id"] == g.user["id"]:
        flash("Nu îți poți șterge propriul cont.", "error")
    elif request.form.get("confirm_username", "").strip().lower() != target["username"]:
        flash("Numele de confirmare nu corespunde — contul nu a fost șters.", "error")
    else:
        db.delete_user(target["id"])
        flash(f"Contul {target['username']} și orarul lui au fost șterse.", "ok")
    return redirect(url_for("auth.admin"))
