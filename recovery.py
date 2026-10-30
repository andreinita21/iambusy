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

import re
import secrets

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from markupsafe import escape

import db
import mailer
from auth import (
    LIMIT_LOGIN_PAIR, _hash_token, _limited, absolute_url, client_ip, hash_password,
    login_required, normalize_username, validate_password, verify_password,
)

bp = Blueprint("recovery", __name__)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
EMAIL_MAX = 254
RESET_LIFETIME = 60 * 60            # seconds
VERIFY_LIFETIME = 24 * 60 * 60
LIMIT_FORGOT_IP = (5, 15 * 60)      # forgot-password requests per visitor
LIMIT_FORGOT_USER = (3, 60 * 60)    # reset emails per account
LIMIT_VERIFY_USER = (5, 60 * 60)    # confirmation emails per account


def normalize_email(raw) -> str:
    return (raw or "").strip().lower()[:EMAIL_MAX + 1]


def valid_email(email: str) -> bool:
    return len(email) <= EMAIL_MAX and bool(EMAIL_RE.match(email))


def _new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _hash_token(token)


# ════════════════════════════════════════════════════ Messages ══

def _message(greeting: str, lines: list[str], button: str = "", url: str = "",
             footer: str = "") -> tuple[str, str]:
    """Plain-text and HTML bodies of one short email."""
    text = "\n\n".join([greeting, *lines, *([f"{button}: {url}"] if url else []),
                        *([footer] if footer else []), "— IamBusy"])
    paragraphs = "".join(f'<p style="margin:0 0 14px">{escape(l)}</p>' for l in lines)
    action = (f'<p style="margin:22px 0"><a href="{escape(url)}" style="display:inline-block;'
              'background:#ffd23f;color:#111;border:3px solid #111;border-radius:8px;'
              'padding:12px 20px;font-weight:700;text-decoration:none">'
              f'{escape(button)}</a></p><p style="margin:0 0 14px;font-size:13px;color:#555">'
              f'Sau copiază linkul în browser:<br>{escape(url)}</p>') if url else ""
    foot = f'<p style="margin:0 0 14px;font-size:13px;color:#555">{escape(footer)}</p>' if footer else ""
    html = ('<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:15px;'
            'line-height:1.5;color:#111;max-width:520px;margin:0 auto;padding:24px">'
            '<p style="margin:0 0 18px;font-size:20px;font-weight:700">IamBusy</p>'
            f'<p style="margin:0 0 14px">{escape(greeting)}</p>{paragraphs}{action}{foot}'
            '<p style="margin:22px 0 0;font-size:13px;color:#555">— IamBusy</p></div>')
    return text, html


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

@bp.route("/account/email", methods=["POST"])
@login_required
def account_email():
    user = g.user
    if not mailer.enabled():
        flash("Trimiterea de emailuri nu este configurată încă.", "error")
        return redirect(url_for("auth.account") + "#email")
    email = normalize_email(request.form.get("email"))
    password = request.form.get("password") or ""
    key = f"email-pw:{user['id']}"
    if _limited(key, LIMIT_LOGIN_PAIR):
        flash("Prea multe încercări. Așteaptă câteva minute.", "error")
    elif not verify_password(user["password_hash"], password):
        db.record_attempt(key)
        flash("Parola nu e corectă.", "error")
    elif not valid_email(email):
        flash("Adresa de email nu pare validă.", "error")
    elif email == user["email"]:
        flash("Aceasta este deja adresa contului.", "ok")
    elif _limited(f"email-verify:{user['id']}", LIMIT_VERIFY_USER):
        flash("Ai cerut prea multe confirmări. Încearcă din nou peste o oră.", "error")
    else:
        db.record_attempt(f"email-verify:{user['id']}")
        token, token_hash = _new_token()
        db.create_email_token(user["id"], "verify", token_hash, email, VERIFY_LIFETIME)
        url = absolute_url(url_for("recovery.verify_email", token=token))
        _send(email, "Confirmă adresa de email pentru IamBusy", _message(
            f"Salut, {user['display_name']}!",
            [f"Confirmă că această adresă îți aparține, ca s-o legăm de contul @{user['username']}. "
             "O folosim doar ca să-ți poți reseta parola.",
             "Linkul funcționează 24 de ore."],
            "Confirmă adresa", url,
            "Dacă nu știi despre ce e vorba, ignoră mesajul — adresa nu va fi folosită."))
        flash(f"Ți-am trimis un link de confirmare la {email}. Verifică și folderul Spam.", "ok")
    return redirect(url_for("auth.account") + "#email")


@bp.route("/account/email/verify/<token>")
def verify_email(token):
    record = db.get_email_token("verify", _hash_token(token)) if len(token) < 100 else None
    target = url_for("auth.account") + "#email" if g.user else url_for("auth.login")
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


@bp.route("/account/email/remove", methods=["POST"])
@login_required
def account_email_remove():
    db.update_user(g.user["id"], email=None)
    db.drop_email_tokens(g.user["id"])
    flash("Adresa de email a fost ștearsă din cont.", "ok")
    return redirect(url_for("auth.account") + "#email")

