"""
Email address on the account and "forgot password" by email.

* The address is optional and only attached after its owner clicks the
  confirmation link we sent to it — so nobody can point an account they
  hijacked, or someone else's address, at a mailbox they don't control.
* "Forgot password" answers the same way whether or not the account / address
  exists, and the email goes out on a background thread, so neither the text
  nor the timing reveals who has an account.
* Links carry 256-bit random tokens; only their SHA-256 is stored.  A reset
  link works once, for an hour, and a new one replaces the old.  Resetting
  logs every device out.
"""

import secrets

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

import captcha
import db
import emails
import mailer
from auth import (
    EMAIL_MAX, LIMIT_LOGIN_PAIR, _hash_token, _limited, absolute_url, client_ip, hash_password,
    login_required, normalize_email, normalize_username, valid_email, validate_password,
    verify_password,
)

bp = Blueprint("recovery", __name__)

RESET_LIFETIME = 60 * 60            # seconds
VERIFY_LIFETIME = 24 * 60 * 60
LIMIT_FORGOT_IP = (5, 15 * 60)      # forgot-password requests per visitor
LIMIT_FORGOT_USER = (3, 60 * 60)    # reset emails per account
LIMIT_VERIFY_USER = (5, 60 * 60)    # confirmation emails per account


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


# ════════════════════════════════════════════════════ Messages ══

_message = mailer.compose


def _send(to: str, subject: str, body: tuple[str, str]) -> None:
    mailer.send(to, subject, *body)


# ═════════════════════════════════════════════ Forgot password ══

@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    if g.user is not None:
        return redirect(url_for("auth.account"))
    sent = False
    value = ""
    if request.method == "POST" and mailer.enabled():
        value = (request.form.get("login") or "").strip()[:EMAIL_MAX]
        ip_key = f"forgot-ip:{client_ip()}"
        if _limited(ip_key, LIMIT_FORGOT_IP):
            flash("Prea multe cereri. Încearcă din nou peste câteva minute.", "error")
            return render_template("forgot.html", sent=False, value=value,
                                   enabled=True), 429
        db.record_attempt(ip_key)
        if not captcha.verify(request.form.get("cf-turnstile-response"), client_ip()):
            flash("Confirmă că nu ești robot (bifa de deasupra butonului).", "error")
            return render_template("forgot.html", sent=False, value=value, enabled=True), 400
        if "@" in value:
            user = db.get_user_by_email(normalize_email(value))
        else:
            user = db.get_user_by_username(normalize_username(value)[:64])
        user_key = f"forgot-user:{user['id']}" if user else ""
        if user and user["is_active"] and user["email"] and not _limited(user_key, LIMIT_FORGOT_USER):
            db.record_attempt(user_key)
            token, token_hash = _new_token()
            db.create_email_token(user["id"], "reset", token_hash, user["email"], RESET_LIFETIME)
            url = absolute_url(url_for("recovery.reset_password", token=token))
            _send(user["email"], "Resetează parola IamBusy", _message(
                f"Salut, {user['display_name']}!",
                [f"Cineva (sperăm că tu) a cerut resetarea parolei contului @{user['username']}.",
                 "Linkul de mai jos funcționează o singură dată, timp de o oră."],
                "Alege o parolă nouă", url,
                "Dacă n-ai cerut tu, ignoră mesajul: parola rămâne cea de acum."))
        sent = True  # the same answer whether or not anything was sent
    return render_template("forgot.html", sent=sent, value=value, enabled=mailer.enabled())


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    record = db.get_email_token("reset", _hash_token(token)) if len(token) < 100 else None
    if record is None:
        return render_template("reset.html", valid=False), 404
    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("password_confirm") or ""
        error = validate_password(password, record["username"], record["display_name"])
        if error is None and password != confirm:
            error = "Parolele nu coincid."
        if error:
            flash(error, "error")
            return render_template("reset.html", valid=True, username=record["username"]), 400
        if not db.use_email_token(record["id"]):
            return render_template("reset.html", valid=False), 404
        user_id = record["user_id"]
        db.update_user(user_id, password_hash=hash_password(password))
        db.delete_user_sessions(user_id)
        db.drop_email_tokens(user_id, "reset")
        db.clear_attempts(f"login-user:{record['username']}")
        _send(record["email"], "Parola IamBusy a fost schimbată", _message(
            f"Salut, {record['display_name']}!",
            [f"Parola contului @{record['username']} tocmai a fost schimbată prin linkul de "
             "resetare, iar toate dispozitivele au fost deconectate.",
             "Dacă n-ai fost tu, scrie-ne imediat răspunzând la acest mesaj."]))
        flash("Parola a fost schimbată. Autentifică-te cu parola nouă.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("reset.html", valid=True, username=record["username"])


# ════════════════════════════════════════ Email on the account ══

def send_verification(user: dict, email: str) -> None:
    """Email a confirmation link for *email* (24 h); a newer link replaces older ones."""
    token, token_hash = _new_token()
    db.create_email_token(user["id"], "verify", token_hash, email, VERIFY_LIFETIME)
    db.record_attempt(f"email-verify:{user['id']}")
    url = absolute_url(url_for("recovery.verify_email", token=token))
    _send(email, "Confirmă adresa de email pentru IamBusy", _message(
        f"Salut, {user['display_name']}!",
        [f"Confirmă că această adresă îți aparține, ca s-o legăm de contul @{user['username']}. "
         "O folosim pentru resetarea parolei și pentru notificările pe care le alegi.",
         "Linkul funcționează 24 de ore."],
        "Confirmă adresa", url,
        "Dacă nu știi despre ce e vorba, ignoră mesajul — adresa nu va fi folosită."))


def _back() -> str:
    """Where the email forms return to: the gate while there is no confirmed address."""
    return url_for("auth.account") + "#email" if g.user["email"] else url_for(
        "recovery.email_required")


@bp.route("/account/email", methods=["POST"])
@login_required
def account_email():
    user = g.user
    if not mailer.enabled():
        flash("Trimiterea de emailuri nu este configurată încă.", "error")
        return redirect(_back())
    email = normalize_email(request.form.get("email"))
    password = request.form.get("password") or ""
    key = f"email-pw:{user['id']}"
    # Replacing a confirmed address needs the password (a stolen session must not
    # be able to redirect password resets); adding the first one doesn't.
    if user["email"] and _limited(key, LIMIT_LOGIN_PAIR):
        flash("Prea multe încercări. Așteaptă câteva minute.", "error")
    elif user["email"] and not verify_password(user["password_hash"], password):
        db.record_attempt(key)
        flash("Parola nu e corectă.", "error")
    elif not valid_email(email):
        flash("Adresa de email nu pare validă.", "error")
    elif email == user["email"]:
        flash("Aceasta este deja adresa contului.", "ok")
    elif _limited(f"email-verify:{user['id']}", LIMIT_VERIFY_USER):
        flash("Ai cerut prea multe confirmări. Încearcă din nou peste o oră.", "error")
    else:
        send_verification(user, email)
        flash(f"Ți-am trimis un link de confirmare la {email}. Verifică și folderul Spam.", "ok")
    return redirect(_back())


@bp.route("/confirm-email/resend", methods=["POST"])
@login_required
def resend_verification():
    pending = db.pending_email(g.user["id"])
    if not pending:
        flash("Nu există nicio adresă care să aștepte confirmarea. Introdu una.", "error")
    elif _limited(f"email-verify:{g.user['id']}", LIMIT_VERIFY_USER):
        flash("Ai cerut prea multe confirmări. Încearcă din nou peste o oră.", "error")
    else:
        send_verification(g.user, pending)
        flash(f"Am retrimis linkul la {pending}.", "ok")
    return redirect(_back())


# ═════════════════════════════════════════ Email is required ══

# Accounts created before this date get the "IamBusy now needs an email" text.
EMAIL_REQUIRED_SINCE = 1793311200  # 2026-10-30 00:00 (Bucharest)

# What an account without a confirmed address may still do.
GATE_OPEN = {
    "static", "recovery.email_required", "recovery.account_email", "recovery.resend_verification",
    "recovery.verify_email", "auth.logout", "auth.account_export", "auth.account_delete",
    "social.email_action", "social.unsubscribe",
}


def require_email():
    """``before_request`` hook: logged-in accounts need a confirmed address."""
    user = g.get("user")
    if user is None or user["email"] or request.endpoint is None or not mailer.enabled():
        return None
    if (request.endpoint in GATE_OPEN or request.endpoint.startswith("public.")
            or request.endpoint.startswith("sharing.share_")):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Confirmă-ți adresa de email ca să continui.",
                        "code": "email"}), 403
    return redirect(url_for("recovery.email_required"))


@bp.route("/confirm-email")
@login_required
def email_required():
    if g.user["email"]:
        return redirect(url_for("index"))
    return render_template(
        "email_required.html", user=g.user, pending=db.pending_email(g.user["id"]),
        is_new=g.user["created_at"] >= EMAIL_REQUIRED_SINCE, mail_enabled=mailer.enabled())


@bp.route("/account/email/verify/<token>")
def verify_email(token):
    record = db.get_email_token("verify", _hash_token(token)) if len(token) < 100 else None
    if g.user is None:
        target = url_for("auth.login")
    elif g.user["email"]:
        target = url_for("auth.account") + "#email"
    else:
        target = url_for("index")  # first confirmed address: the app opens up
    if record is None:
        flash("Linkul de confirmare nu mai este valid. Cere unul nou din pagina contului.", "error")
        return redirect(target)
    old = record["current_email"]
    if not db.set_verified_email(record["user_id"], record["email"]):
        flash("Această adresă este deja folosită de alt cont.", "error")
        return redirect(target)
    db.use_email_token(record["id"])
    db.drop_email_tokens(record["user_id"], "reset")  # links sent to the old address
    if old and old != record["email"]:
        _send(old, "Adresa de email IamBusy a fost schimbată", _message(
            f"Salut, {record['display_name']}!",
            [f"Contul @{record['username']} folosește de acum altă adresă de email "
             "pentru resetarea parolei.",
             "Dacă n-ai fost tu, schimbă-ți parola și scrie-ne răspunzând la acest mesaj."]))
    flash(f"Adresa {record['email']} a fost confirmată.", "ok")
    return redirect(target)



@bp.route("/account/notifications", methods=["POST"])
@login_required
def account_notifications():
    kinds = [k for k in emails.KINDS if request.form.get(f"notify_{k}") == "1"]
    db.update_user(g.user["id"], email_notify=",".join(kinds))
    flash("Preferințele de email au fost salvate.", "ok")
    return redirect(url_for("auth.account") + "#notifications")
