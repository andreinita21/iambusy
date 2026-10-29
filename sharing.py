"""
Public share links for IamBusy — let anyone see (part of) a schedule
without an account.

Each link has
* a **preset** — ``busy`` (only busy / free), ``full`` (all details) or
  ``friends`` (exactly what "all friends" see) — optionally adjusted per
  category; per-activity "all friends" overrides can only make it more
  private (see :func:`visibility.link_level`);
* a **date window** — unlimited, a fixed range, a rolling "next N days"
  or hand-picked days; dates outside it are empty and the status / next
  activity look-ahead never reaches past it;
* an optional **expiry**, an optional **PIN** (scrypt-hashed, rate
  limited per IP and per link), a live-status switch and view statistics.

The 128-bit token in the URL is the only credential; it can be
regenerated, and the link disabled or deleted, at any time.
"""

import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Optional

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import db
import db_social
from auth import absolute_url, client_ip, login_required, _limited
from calendar_data import Calendar, Viewer, Window, build_day_context, day_summaries, parse_range
from schedule_engine import format_date_ro
from visibility import LEVEL_LABELS, LEVELS

bp = Blueprint("sharing", __name__)

_settings = {"default_week1_start": date.today(), "cookie_secure": False}

PRESETS = {
    "busy": "Doar ocupat / liber",
    "full": "Toate detaliile",
    "friends": "Ca prietenii (regula „Toți prietenii”)",
}
WINDOW_TYPES = {
    "unlimited": "Oricând",
    "range": "Între două date",
    "rolling": "Următoarele N zile",
    "days": "Doar anumite zile",
}
MAX_SHARE_LINKS = 50
MAX_DAYS_LIST = 90
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
PIN_RE = re.compile(r"^\d{4,8}$")
LIMIT_PIN_IP = (5, 15 * 60)     # one visitor guessing one link's PIN
LIMIT_PIN_LINK = (30, 60 * 60)  # many visitors guessing one link's PIN
PIN_HASH = "scrypt:32768:8:1"


def configure(*, default_week1_start, cookie_secure) -> None:
    _settings.update(default_week1_start=default_week1_start, cookie_secure=cookie_secure)


# ═══════════════════════════════════════════════════ Helpers ══

def link_window(link: dict, today: date) -> Optional[Window]:
    kind = link["window_type"]
    if kind == "range":
        return Window(date.fromisoformat(link["range_from"]), date.fromisoformat(link["range_to"]))
    if kind == "rolling":
        return Window(today, today + timedelta(days=max(1, link["rolling_days"] or 1) - 1))
    if kind == "days":
        return Window(days=[date.fromisoformat(d) for d in link["days"]])
    return None


def describe_window(link: dict) -> str:
    kind = link["window_type"]
    if kind == "range":
        a, b = date.fromisoformat(link["range_from"]), date.fromisoformat(link["range_to"])
        return f"{format_date_ro(a)} {a.year} – {format_date_ro(b)} {b.year}"
    if kind == "rolling":
        n = link["rolling_days"]
        return "doar azi" if n == 1 else f"azi + următoarele {n - 1} zile"
    if kind == "days":
        days = [date.fromisoformat(d) for d in link["days"]]
        shown = ", ".join(format_date_ro(d) for d in days[:6])
        return shown + (f" și încă {len(days) - 6}" if len(days) > 6 else "")
    return "oricând"


def link_state(link: Optional[dict]) -> str:
    if link is None or not link.get("owner_active", 1):
        return "missing"
    if link["revoked"]:
        return "revoked"
    if link["expires_at"] is not None and link["expires_at"] <= int(datetime.now().timestamp()):
        return "expired"
    return "ok"


def _fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else "—"


def _pin_marker(link: dict) -> str:
    """What the visitor's session remembers after typing the right PIN.

    Derived from the PIN hash, so changing the PIN locks everyone out again.
    """
    return hashlib.sha256(f"{link['id']}:{link['pin_hash']}".encode()).hexdigest()[:20]


def _unlocked(link: dict) -> bool:
    if not link["pin_hash"]:
        return True
    if g.user is not None and g.user["id"] == link["user_id"]:
        return True
    return session.get("share_pins", {}).get(str(link["id"])) == _pin_marker(link)


def _is_owner(link: dict) -> bool:
    return g.user is not None and g.user["id"] == link["user_id"]


def _load(token: str):
    """``(link, state)`` for a token from the URL."""
    if not TOKEN_RE.match(token or ""):
        return None, "missing"
    link = db_social.get_share_by_token(token)
    return link, link_state(link)


# ══════════════════════════════════════════════ Form parsing ══

def _parse_form(form, categories: list[dict]):
    """Validate the create / edit form → ``(fields, rules, pin_hash, clear_pin, error)``."""
    fields = {
        "label": " ".join((form.get("label") or "").split())[:60],
        "preset": form.get("preset") if form.get("preset") in PRESETS else "busy",
        "window_type": form.get("window_type") if form.get("window_type") in WINDOW_TYPES
        else "unlimited",
        "range_from": None, "range_to": None, "rolling_days": None, "days_list": "",
        "expires_at": None,
        "show_status": 1 if form.get("show_status") == "1" else 0,
    }

    def parse_day(raw):
        try:
            d = date.fromisoformat((raw or "").strip())
        except ValueError:
            return None
        return d if 2000 <= d.year <= 2100 else None

    kind = fields["window_type"]
    if kind == "range":
        a, b = parse_day(form.get("range_from")), parse_day(form.get("range_to"))
        if a is None or b is None or b < a:
            return None, None, None, False, "Alege data de început și de sfârșit (sfârșitul după început)."
        if (b - a).days > 3660:
            return None, None, None, False, "Intervalul poate avea cel mult 10 ani."
        fields["range_from"], fields["range_to"] = a.isoformat(), b.isoformat()
    elif kind == "rolling":
        try:
            n = int(form.get("rolling_days") or 0)
        except ValueError:
            n = 0
        if not 1 <= n <= 365:
            return None, None, None, False, "Numărul de zile trebuie să fie între 1 și 365."
        fields["rolling_days"] = n
    elif kind == "days":
        raw = re.split(r"[\s,;]+", form.get("days_list") or "")
        days = sorted({d for d in (parse_day(x) for x in raw if x) if d})
        if not days or len(days) > MAX_DAYS_LIST:
            return None, None, None, False, f"Alege între 1 și {MAX_DAYS_LIST} de zile."
        fields["days_list"] = json.dumps([d.isoformat() for d in days])

    if form.get("expires") == "at":
        try:
            when = datetime.strptime(form.get("expires_at") or "", "%Y-%m-%dT%H:%M")
        except ValueError:
            return None, None, None, False, "Data de expirare nu este validă."
        if when <= datetime.now():
            return None, None, None, False, "Data de expirare trebuie să fie în viitor."
        fields["expires_at"] = int(when.timestamp())

    rules = {}
    for c in categories:
        value = form.get(f"level-{c['id']}", "")
        if value in LEVELS:
            rules[c["id"]] = value

    pin = (form.get("pin") or "").strip()
    pin_hash = None
    clear_pin = form.get("clear_pin") == "1"
    if pin:
        if not PIN_RE.match(pin):
            return None, None, None, False, "PIN-ul are 4–8 cifre."
        pin_hash = generate_password_hash(pin, method=PIN_HASH)
        clear_pin = False
    return fields, rules, pin_hash, clear_pin, None


# ═══════════════════════════════════════════════ Owner pages ══

@bp.route("/shares")
@login_required
def shares():
    me = g.user
    categories = db.list_categories(me["id"])
    links = db_social.list_share_links(me["id"])
    for link in links:
        link["url"] = absolute_url(url_for("sharing.share_view", token=link["token"]))
        link["state"] = link_state({**link, "owner_active": 1})
        link["window_desc"] = describe_window(link)
        link["expires_str"] = _fmt_ts(link["expires_at"]) if link["expires_at"] else "nu expiră"
        link["last_viewed_str"] = _fmt_ts(link["last_viewed_at"])
        link["preset_label"] = PRESETS[link["preset"]]
        link["tweaks"] = len(db_social.share_rules(link["id"]))

    editing = None
    edit_id = request.args.get("edit", type=int)
    if edit_id:
        editing = db_social.get_share_link(me["id"], edit_id)
        if editing is None:
            abort(404)
        editing["rules"] = db_social.share_rules(edit_id)
        editing["expires_local"] = datetime.fromtimestamp(editing["expires_at"]).strftime(
            "%Y-%m-%dT%H:%M") if editing["expires_at"] else ""

    return render_template(
        "shares.html",
        user=me,
        links=links,
        categories=categories,
        editing=editing,
        presets=PRESETS,
        window_types=WINDOW_TYPES,
        levels=LEVELS,
        level_labels=LEVEL_LABELS,
        today=datetime.now().date().isoformat(),
    )


@bp.route("/shares", methods=["POST"])
@login_required
def share_create():
    me = g.user
    if db_social.count_rows("share_links", "user_id", me["id"]) >= MAX_SHARE_LINKS:
        flash(f"Poți avea cel mult {MAX_SHARE_LINKS} de linkuri. Șterge unul vechi.", "error")
        return redirect(url_for("sharing.shares"))
    fields, rules, pin_hash, _clear, error = _parse_form(request.form, db.list_categories(me["id"]))
    if error:
        flash(error, "error")
        return redirect(url_for("sharing.shares") + "#new")
    db_social.create_share_link(me["id"], secrets.token_urlsafe(16), fields, pin_hash, rules)
    flash("Link creat — copiază-l și trimite-l cui vrei.", "ok")
    return redirect(url_for("sharing.shares"))


@bp.route("/shares/<int:link_id>", methods=["POST"])
@login_required
def share_update(link_id):
    me = g.user
    if db_social.get_share_link(me["id"], link_id) is None:
        abort(404)
    fields, rules, pin_hash, clear_pin, error = _parse_form(
        request.form, db.list_categories(me["id"]))
    if error:
        flash(error, "error")
        return redirect(url_for("sharing.shares", edit=link_id) + "#new")
    db_social.update_share_link(me["id"], link_id, fields, rules, pin_hash, clear_pin)
    flash("Link actualizat. Cine îl are vede de acum noile setări.", "ok")
    return redirect(url_for("sharing.shares"))


@bp.route("/shares/<int:link_id>/toggle", methods=["POST"])
@login_required
def share_toggle(link_id):
    link = db_social.get_share_link(g.user["id"], link_id)
    if link is None:
        abort(404)
    db_social.set_share_revoked(g.user["id"], link_id, not link["revoked"])
    flash("Link reactivat." if link["revoked"] else "Link dezactivat — nu mai poate fi deschis.", "ok")
    return redirect(url_for("sharing.shares"))


@bp.route("/shares/<int:link_id>/regenerate", methods=["POST"])
@login_required
def share_regenerate(link_id):
    if db_social.regenerate_share_token(g.user["id"], link_id, secrets.token_urlsafe(16)):
        flash("Link nou generat — vechiul link nu mai funcționează.", "ok")
    return redirect(url_for("sharing.shares"))


@bp.route("/shares/<int:link_id>/delete", methods=["POST"])
@login_required
def share_delete(link_id):
    if db_social.delete_share_link(g.user["id"], link_id):
        flash("Link șters.", "ok")
    return redirect(url_for("sharing.shares"))


# ═══════════════════════════════════════════════ Public view ══

def _gone(state: str):
    status = 410 if state in ("revoked", "expired") else 404
    response = render_template("share_gone.html", state=state)
    return response, status, {"X-Robots-Tag": "noindex, nofollow"}


@bp.route("/s/<token>")
def share_view(token):
    link, state = _load(token)
    if state != "ok":
        return _gone(state)
    if not _unlocked(link):
        return render_template("share_pin.html", token=token), 200, \
            {"X-Robots-Tag": "noindex, nofollow"}

    if not _is_owner(link):
        seen = session.get("share_seen", [])
        if link["id"] not in seen:
            db_social.record_share_view(link["id"])
            session["share_seen"] = (seen + [link["id"]])[-50:]
            session.permanent = True

    owner = db.get_user(link["user_id"])
    now = datetime.now()
    window = link_window(link, now.date())
    try:
        view_date = datetime.strptime(request.args.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        view_date = window.clamp(now.date()) if window else now.date()

    cal = Calendar(owner, _settings["default_week1_start"])
    context = build_day_context(cal, Viewer.link(link), view_date, now,
                                name=owner["display_name"], window=window,
                                show_status=bool(link["show_status"]))
    context.update(
        mode="share",
        username=owner["username"],
        api_days=url_for("sharing.share_days", token=token),
        share_label=link["label"],
        share_window=describe_window(link),
        share_expires=_fmt_ts(link["expires_at"]) if link["expires_at"] else "",
        is_owner=_is_owner(link),
    )
    return render_template("index.html", context=context), 200, \
        {"X-Robots-Tag": "noindex, nofollow"}


@bp.route("/s/<token>/pin", methods=["POST"])
def share_pin(token):
    link, state = _load(token)
    if state != "ok":
        return _gone(state)
    if not link["pin_hash"]:
        return redirect(url_for("sharing.share_view", token=token))
    ip_key = f"pin:{link['id']}:{client_ip()}"
    link_key = f"pin:{link['id']}"
    if _limited(ip_key, LIMIT_PIN_IP) or _limited(link_key, LIMIT_PIN_LINK):
        flash("Prea multe încercări. Așteaptă câteva minute.", "error")
        return render_template("share_pin.html", token=token), 429
    pin = (request.form.get("pin") or "").strip()
    if PIN_RE.match(pin) and check_password_hash(link["pin_hash"], pin):
        pins = dict(session.get("share_pins", {}))
        pins[str(link["id"])] = _pin_marker(link)
        session["share_pins"] = dict(list(pins.items())[-20:])
        session.permanent = True
        db.clear_attempts(ip_key)
        return redirect(url_for("sharing.share_view", token=token))
    db.record_attempt(ip_key, link_key)
    flash("PIN greșit.", "error")
    return render_template("share_pin.html", token=token), 401


@bp.route("/s/<token>/days")
def share_days(token):
    link, state = _load(token)
    if state != "ok":
        return jsonify({"error": "Linkul nu mai este valid.", "code": "gone"}), 404
    if not _unlocked(link):
        return jsonify({"error": "Introdu PIN-ul.", "code": "pin"}), 403
    start, end, error = parse_range(request.args)
    if error:
        return jsonify({"error": error}), 400
    owner = db.get_user(link["user_id"])
    cal = Calendar(owner, _settings["default_week1_start"])
    window = link_window(link, datetime.now().date())
    return jsonify({
        "today": datetime.now().date().isoformat(),
        "academic_start": cal.week1.isoformat(),
        "days": day_summaries(cal, Viewer.link(link), start, end, window),
    })
