"""
Friends for IamBusy — requests, friend calendars, groups, visibility rules,
activity invites, notifications, friend links and the common free time
finder.

Access model
------------
* Only accepted friends can open each other's calendars; everything they
  see goes through :class:`calendar_data.Calendar` with a friend
  :class:`~calendar_data.Viewer`, so masking happens in one place.
* Search needs the exact username and is rate limited; blocked users look
  like unknown users.
* Groups and rules are private to their owner and are only ever read for
  the logged-in user.
"""

import re
import secrets
import sqlite3
import time
from datetime import date, datetime, timedelta

from flask import (
    Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for,
)

import db
import db_social
from auth import (
    absolute_url, client_ip, friend_signup_enabled, login_required, normalize_friend_code,
    normalize_username, _limited,
)
from calendar_data import (
    Calendar, Viewer, build_day_context, day_summaries, live_status, parse_range, view_date,
)
from schedule_engine import (
    DAY_DISPLAY, DAYS, find_conflicts, format_date_ro, free_between, merge_intervals, minutes_to_hhmm,
)
from visibility import DEFAULT_LEVEL, LEVEL_LABELS, LEVELS, friend_level

bp = Blueprint("social", __name__)

_settings = {"default_week1_start": date.today()}

NAME_MAX = 40
MAX_CATEGORIES = 30
MAX_GROUPS = 30
MAX_FRIEND_LINKS = 20
MAX_REQUESTS_PER_DAY = 30
LIMIT_SEARCH = (40, 10 * 60)        # exact-username lookups per user
LIMIT_FRIEND_LINK_IP = (20, 60 * 60)
MAX_BUSY_PEOPLE = 10
WEEK_LABELS = {"both": "", "odd": "săpt. impară", "even": "săpt. pară"}


def configure(*, default_week1_start) -> None:
    _settings["default_week1_start"] = default_week1_start


def _default_week1() -> date:
    return _settings["default_week1_start"]


# ═══════════════════════════════════════════════════ Helpers ══

def clean_name(raw) -> str:
    return " ".join((raw or "").split())[:NAME_MAX]


def name_error(name: str):
    if not name:
        return "Alege un nume."
    if any(ord(ch) < 32 or ch in "<>" for ch in name):
        return "Numele conține caractere nepermise."
    return None


def clean_color(raw) -> str:
    return raw if raw in db.CATEGORY_COLORS else "cyan"


def _int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def when_label(act: dict) -> str:
    """``Vineri, 7 noiembrie · 18:00–20:00`` or ``Luni · 10:00–12:00 · săpt. impară``."""
    times = f"{act['start_time']}–{act['end_time']}"
    if act.get("date"):
        d = date.fromisoformat(act["date"])
        return f"{DAY_DISPLAY[DAYS[d.weekday()]]}, {format_date_ro(d, long=True)} {d.year} · {times}"
    parts = [f"În fiecare {DAY_DISPLAY[act['day']].lower()}", times]
    if WEEK_LABELS.get(act.get("week_type")):
        parts.append(WEEK_LABELS[act["week_type"]])
    if act.get("valid_from") or act.get("valid_until"):
        start = format_date_ro(date.fromisoformat(act["valid_from"])) if act.get("valid_from") else "…"
        end = format_date_ro(date.fromisoformat(act["valid_until"])) if act.get("valid_until") else "…"
        parts.append(f"{start} – {end}")
    return " · ".join(parts)


def fmt_ts(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


NOTIFICATION_TEXT = {
    "friend_accepted": "{actor} a acceptat cererea ta de prietenie.",
    "friend_joined": "{actor} a folosit linkul tău de prietenie — acum sunteți prieteni.",
    "invite_accepted": "{actor} vine la „{text}”.",
    "invite_declined": "{actor} a refuzat invitația la „{text}”.",
    "participant_left": "{actor} a renunțat la „{text}”.",
    "activity_invite": "{actor} te-a invitat la „{text}”.",
    "activity_changed": "{actor} a modificat „{text}”.",
    "activity_cancelled": "{actor} a anulat „{text}”.",
}


def describe_notifications(items: list[dict]) -> list[dict]:
    for n in items:
        template = NOTIFICATION_TEXT.get(n["kind"], "{text}")
        n["message"] = template.format(actor=n["actor_name"] or "Cineva", text=n["text"])
        n["when"] = fmt_ts(n["created_at"])
        n["unread"] = n["read_at"] is None
    return items


def friend_or_404(username: str) -> dict:
    """The friend called *username* — 404 for strangers, so nobody can probe."""
    other = db.get_user_by_username(normalize_username(username)[:64])
    if other is None or not other["is_active"] or not db_social.are_friends(g.user["id"], other["id"]):
        abort(404)
    return other


def _redirect_back(default: str):
    target = request.form.get("back") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(default)


# ═══════════════════════════════════════════════════════ Hub ══

def _invite_rows(user: dict) -> list[dict]:
    invites = db_social.pending_invites(user["id"])
    if not invites:
        return []
    week1 = Calendar(user, _default_week1()).week1
    mine = db.get_all_activities(user["id"]) + db_social.shared_activities(
        user["id"], statuses=("accepted",))
    categories = db.list_categories(user["id"])
    by_name = {c["name"]: c["id"] for c in categories}
    fallback = db.fallback_category_id(user["id"])
    people = db_social.participants(i["id"] for i in invites)
    for inv in invites:
        inv["when"] = when_label(inv)
        inv["conflicts"] = [c["title"] for c in find_conflicts(inv, mine, week1)]
        inv["participants"] = people.get(inv["id"], [])
        owner_cat = db.get_category(inv["owner_id"], inv["owner_category_id"]) \
            if inv.get("owner_category_id") else None
        inv["suggested_category"] = (by_name.get(owner_cat["name"]) if owner_cat else None) \
            or fallback
    return invites


@bp.route("/friends")
@login_required
def friends():
    me = g.user
    now = datetime.now()

    # ── exact-username lookup ────────────────────────────────
    query = normalize_username(request.args.get("u"))[:64]
    result = None
    if query:
        key = f"search:{me['id']}"
        if _limited(key, LIMIT_SEARCH):
            flash("Prea multe căutări. Încearcă din nou în câteva minute.", "error")
        else:
            db.record_attempt(key)
            other = db.get_user_by_username(query)
            if other and other["is_active"] and other["id"] != me["id"] \
                    and not db_social.is_blocked(me["id"], other["id"]):
                f = db_social.friendship_between(me["id"], other["id"])
                if f is None:
                    relation = "none"
                elif f["status"] == "accepted":
                    relation = "friends"
                else:
                    relation = "outgoing" if f["requester_id"] == me["id"] else "incoming"
                result = {"id": other["id"], "username": other["username"],
                          "display_name": other["display_name"], "relation": relation}
            elif other and other["id"] == me["id"]:
                result = {"self": True}
            else:
                result = {"missing": True}

    friends_list = db_social.list_friends(me["id"])
    groups = db_social.list_groups(me["id"])
    group_names = {gr["id"]: gr for gr in groups}
    member_of: dict = {}
    for gr in groups:
        for m in gr["member_ids"]:
            member_of.setdefault(m, []).append(gr)
    for f in friends_list:
        cal = Calendar(f, _default_week1())
        f["status"] = live_status(cal, Viewer.friend(f["id"], me["id"]), now, f["display_name"])
        f["groups"] = member_of.get(f["id"], [])

    incoming, outgoing = db_social.list_requests(me["id"])
    notifications = describe_notifications(db_social.list_notifications(me["id"], limit=8))
    db_social.mark_notifications_read(me["id"])

    links = db_social.list_friend_links(me["id"])
    for link in links:
        link["url"] = absolute_url(url_for("social.friend_link", code=link["code"]))
        link["expires_str"] = fmt_ts(link["expires_at"]) if link["expires_at"] else "nu expiră"

    return render_template(
        "friends.html",
        user=me,
        query=query,
        result=result,
        friends=friends_list,
        group_names=group_names,
        incoming=incoming,
        outgoing=outgoing,
        invites=_invite_rows(me),
        categories=db.list_categories(me["id"]),
        notifications=notifications,
        links=links,
        signup_via_links=friend_signup_enabled(),
        blocked=db_social.list_blocked(me["id"]),
    )


@bp.route("/notifications")
@login_required
def notifications():
    items = describe_notifications(db_social.list_notifications(g.user["id"], limit=200))
    db_social.mark_notifications_read(g.user["id"])
    return render_template("notifications.html", user=g.user, notifications=items)


# ══════════════════════════════════════════════════ Requests ══

@bp.route("/friends/request", methods=["POST"])
@login_required
def friend_request():
    me = g.user
    username = normalize_username(request.form.get("username"))[:64]
    # Counts as a lookup: the answer tells whether the username exists.
    key = f"search:{me['id']}"
    if _limited(key, LIMIT_SEARCH):
        flash("Prea multe căutări. Încearcă din nou în câteva minute.", "error")
        return redirect(url_for("social.friends"))
    db.record_attempt(key)
    other = db.get_user_by_username(username)
    if other is None or not other["is_active"] or other["id"] == me["id"]:
        flash("Nu există niciun cont cu acest nume de utilizator.", "error")
        return redirect(url_for("social.friends"))
    if db_social.count_outgoing_today(me["id"]) >= MAX_REQUESTS_PER_DAY:
        flash("Ai trimis prea multe cereri azi. Încearcă mâine.", "error")
        return redirect(url_for("social.friends"))
    outcome = db_social.send_friend_request(me["id"], other["id"])
    messages = {
        "sent": (f"Cerere trimisă către @{other['username']}.", "ok"),
        "accepted": (f"Tu și {other['display_name']} sunteți acum prieteni.", "ok"),
        "already": ("Sunteți deja prieteni.", "ok"),
        "pending": ("Ai trimis deja o cerere — așteaptă răspunsul.", "ok"),
        # Same wording as an unknown user: a block is not advertised.
        "blocked": ("Nu există niciun cont cu acest nume de utilizator.", "error"),
    }
    flash(*messages[outcome])
    return redirect(url_for("social.friends"))


@bp.route("/friends/<int:other_id>/accept", methods=["POST"])
@login_required
def friend_accept(other_id):
    if db_social.accept_friend_request(g.user["id"], other_id):
        flash("Cerere acceptată. Alege din Confidențialitate ce vede noul prieten.", "ok")
    return redirect(url_for("social.friends"))


@bp.route("/friends/<int:other_id>/remove", methods=["POST"])
@login_required
def friend_remove(other_id):
    """Decline, cancel or unfriend — whatever the relation currently is."""
    f = db_social.friendship_between(g.user["id"], other_id)
    if f and db_social.remove_friendship(g.user["id"], other_id):
        if f["status"] == "accepted":
            flash("Prieten eliminat. Nu mai vedeți unul programul celuilalt.", "ok")
        elif f["requester_id"] == g.user["id"]:
            flash("Cerere anulată.", "ok")
        else:
            flash("Cerere refuzată.", "ok")
    return redirect(url_for("social.friends"))


@bp.route("/friends/<int:other_id>/block", methods=["POST"])
@login_required
def friend_block(other_id):
    other = db.get_user(other_id)
    # Only people you already have a request or friendship with — otherwise
    # walking through ids would list every account.
    if other is None or db_social.friendship_between(g.user["id"], other_id) is None:
        abort(404)
    db_social.block_user(g.user["id"], other_id)
    flash(f"@{other['username']} a fost blocat: nu te mai poate găsi și nu îți mai poate trimite "
          "cereri.", "ok")
    return redirect(url_for("social.friends"))


@bp.route("/friends/blocked/<int:other_id>/unblock", methods=["POST"])
@login_required
def friend_unblock(other_id):
    if db_social.unblock_user(g.user["id"], other_id):
        flash("Utilizator deblocat.", "ok")
    return redirect(url_for("social.friends") + "#blocked")


# ═════════════════════════════════════════ Friend calendars ══

def _view_date() -> date:
    return view_date(request.args)


@bp.route("/friends/<username>")
@login_required
def friend_calendar(username):
    other = friend_or_404(username)
    cal = Calendar(other, _default_week1())
    context = build_day_context(cal, Viewer.friend(other["id"], g.user["id"]), _view_date(),
                                datetime.now(), name=other["display_name"])
    context.update(
        mode="friend",
        username=other["username"],
        api_days=url_for("social.api_friend_days", username=other["username"]),
        settings_link=url_for("social.privacy", **{"for": f"f{other['id']}"}),
    )
    return render_template("index.html", context=context)


@bp.route("/preview/<username>")
@login_required
def preview_as(username):
    """My own calendar, exactly as friend *username* sees it."""
    other = friend_or_404(username)
    cal = Calendar(g.user, _default_week1())
    context = build_day_context(cal, Viewer.friend(g.user["id"], other["id"]), _view_date(),
                                datetime.now(), name=g.user["display_name"])
    context.update(
        mode="preview",
        username=g.user["username"],
        viewer_name=other["display_name"],
        api_days=url_for("social.api_preview_days", username=other["username"]),
        settings_link=url_for("social.privacy", **{"for": f"f{other['id']}"}),
    )
    return render_template("index.html", context=context)


def _days_json(cal: Calendar, viewer: Viewer):
    start, end, error = parse_range(request.args)
    if error:
        return jsonify({"error": error}), 400
    return jsonify({
        "today": datetime.now().date().isoformat(),
        "academic_start": cal.week1.isoformat(),
        "days": day_summaries(cal, viewer, start, end),
    })


@bp.route("/api/friends/<username>/days")
@login_required
def api_friend_days(username):
    other = friend_or_404(username)
    return _days_json(Calendar(other, _default_week1()), Viewer.friend(other["id"], g.user["id"]))


@bp.route("/api/preview/<username>/days")
@login_required
def api_preview_days(username):
    other = friend_or_404(username)
    return _days_json(Calendar(g.user, _default_week1()), Viewer.friend(g.user["id"], other["id"]))


# ═════════════════════════════════════════════════ Privacy ══

def _parse_subject(raw: str, friends: list[dict], groups: list[dict]):
    """``all`` / ``g12`` / ``f7`` → ``(subject_type, subject_id, label, object)``."""
    raw = raw or "all"
    m = re.fullmatch(r"([gf])(\d+)", raw)
    if m:
        sid = int(m.group(2))
        if m.group(1) == "g":
            gr = next((x for x in groups if x["id"] == sid), None)
            if gr:
                return "group", sid, f"Grupul {gr['name']}", gr
        else:
            fr = next((x for x in friends if x["id"] == sid), None)
            if fr:
                return "friend", sid, fr["display_name"], fr
    return "all", 0, "Toți prietenii", None


@bp.route("/privacy")
@login_required
def privacy():
    me = g.user
    categories = db.list_categories(me["id"])
    friends_list = db_social.list_friends(me["id"])
    groups = db_social.list_groups(me["id"])
    subject_type, subject_id, subject_label, subject = _parse_subject(
        request.args.get("for"), friends_list, groups)
    rules = db_social.category_rules(me["id"])

    member_groups = []
    if subject_type == "friend":
        member_groups = [gr["id"] for gr in groups if subject_id in gr["member_ids"]]

    rows = []
    for c in categories:
        cat_rules = rules.get(c["id"], {})
        own = cat_rules.get((subject_type, subject_id))
        if subject_type == "friend":
            effective = friend_level({}, cat_rules, subject_id, member_groups)
        elif subject_type == "group":
            effective = cat_rules.get(("group", subject_id)) or cat_rules.get(("all", 0)) \
                or DEFAULT_LEVEL
        else:
            effective = own or DEFAULT_LEVEL
        rows.append({**c, "level": own, "effective": effective})

    return render_template(
        "privacy.html",
        user=me,
        categories=categories,
        rows=rows,
        friends=friends_list,
        groups=groups,
        subject_type=subject_type,
        subject_key="all" if subject_type == "all" else f"{subject_type[0]}{subject_id}",
        subject_label=subject_label,
        subject=subject,
        member_groups=member_groups,
        colors=db.CATEGORY_COLORS,
        levels=LEVELS,
        level_labels=LEVEL_LABELS,
        default_level=DEFAULT_LEVEL,
    )


@bp.route("/privacy/rules", methods=["POST"])
@login_required
def privacy_rules():
    me = g.user
    friends_list = db_social.list_friends(me["id"])
    groups = db_social.list_groups(me["id"])
    subject_type, subject_id, label, _obj = _parse_subject(
        request.form.get("for"), friends_list, groups)
    levels = {}
    for c in db.list_categories(me["id"]):
        value = request.form.get(f"level-{c['id']}", "")
        if value in LEVELS:
            levels[c["id"]] = value
        elif value == "inherit" and subject_type != "all":
            levels[c["id"]] = None
    db_social.set_category_rules(me["id"], subject_type, subject_id, levels)
    if subject_type == "friend" and request.form.get("groups_present") == "1":
        db_social.set_member_groups(
            me["id"], subject_id,
            [gid for gid in (_int(x) for x in request.form.getlist("groups")) if gid is not None])
    flash(f"Reguli salvate pentru: {label}.", "ok")
    key = "all" if subject_type == "all" else f"{subject_type[0]}{subject_id}"
    return redirect(url_for("social.privacy", **{"for": key}))


# ── Categories ───────────────────────────────────────────────

@bp.route("/categories", methods=["POST"])
@login_required
def category_create():
    name = clean_name(request.form.get("name"))
    error = name_error(name)
    if error is None and db_social.count_rows("categories", "user_id", g.user["id"]) >= MAX_CATEGORIES:
        error = f"Poți avea cel mult {MAX_CATEGORIES} de categorii."
    if error is None:
        try:
            db.create_category(g.user["id"], name, clean_color(request.form.get("color")))
            flash(f"Categoria „{name}” a fost creată. Noii prieteni o văd „Doar ocupat” până "
                  "o schimbi.", "ok")
        except sqlite3.IntegrityError:
            error = "Ai deja o categorie cu acest nume."
    if error:
        flash(error, "error")
    return redirect(url_for("social.privacy") + "#categories")


@bp.route("/categories/<int:category_id>", methods=["POST"])
@login_required
def category_update(category_id):
    name = clean_name(request.form.get("name"))
    error = name_error(name)
    if error is None:
        try:
            if not db.update_category(g.user["id"], category_id, name,
                                      clean_color(request.form.get("color"))):
                abort(404)
            flash("Categorie salvată.", "ok")
        except sqlite3.IntegrityError:
            error = "Ai deja o categorie cu acest nume."
    if error:
        flash(error, "error")
    return redirect(url_for("social.privacy") + "#categories")


@bp.route("/categories/<int:category_id>/delete", methods=["POST"])
@login_required
def category_delete(category_id):
    move_to = _int(request.form.get("move_to"))
    if move_to is None or not db.delete_category(g.user["id"], category_id, move_to):
        flash("Alege o altă categorie în care să muți activitățile.", "error")
    else:
        flash("Categorie ștearsă; activitățile ei au fost mutate.", "ok")
    return redirect(url_for("social.privacy") + "#categories")


# ── Groups ───────────────────────────────────────────────────

@bp.route("/groups", methods=["POST"])
@login_required
def group_create():
    name = clean_name(request.form.get("name"))
    error = name_error(name)
    if error is None and db_social.count_rows("friend_groups", "owner_id", g.user["id"]) >= MAX_GROUPS:
        error = f"Poți avea cel mult {MAX_GROUPS} de grupuri."
    if error is None:
        try:
            gid = db_social.create_group(g.user["id"], name, clean_color(request.form.get("color")))
            flash(f"Grupul „{name}” a fost creat. Adaugă membri și alege ce văd.", "ok")
            return redirect(url_for("social.privacy", **{"for": f"g{gid}"}) + "#groups")
        except sqlite3.IntegrityError:
            error = "Ai deja un grup cu acest nume."
    flash(error, "error")
    return redirect(url_for("social.privacy") + "#groups")


@bp.route("/groups/<int:group_id>", methods=["POST"])
@login_required
def group_update(group_id):
    name = clean_name(request.form.get("name"))
    error = name_error(name)
    if error is None:
        try:
            if not db_social.update_group(g.user["id"], group_id, name,
                                          clean_color(request.form.get("color"))):
                abort(404)
        except sqlite3.IntegrityError:
            error = "Ai deja un grup cu acest nume."
    if error is None:
        members = [m for m in (_int(x) for x in request.form.getlist("members")) if m is not None]
        db_social.set_group_members(g.user["id"], group_id, members)
        flash("Grup salvat.", "ok")
    else:
        flash(error, "error")
    return redirect(url_for("social.privacy", **{"for": f"g{group_id}"}) + "#groups")


@bp.route("/groups/<int:group_id>/delete", methods=["POST"])
@login_required
def group_delete(group_id):
    if db_social.delete_group(g.user["id"], group_id):
        flash("Grup șters, împreună cu regulile lui.", "ok")
    return redirect(url_for("social.privacy") + "#groups")


# ═══════════════════════════════════════════ Activity invites ══

@bp.route("/invites/<int:activity_id>/respond", methods=["POST"])
@login_required
def invite_respond(activity_id):
    accept = request.form.get("accept") == "1"
    category_id = _int(request.form.get("category_id"))
    act = db_social.respond_invite(g.user["id"], activity_id, accept, category_id)
    if act is None:
        flash("Invitația nu mai există.", "error")
    elif accept:
        flash(f"„{act['title']}” a fost adăugată în calendarul tău. Prietenii tăi o văd după "
              "regulile categoriei alese.", "ok")
    else:
        flash("Invitație refuzată.", "ok")
    return _redirect_back(url_for("social.friends"))


# ═════════════════════════════════════════════════ Friend links ══

@bp.route("/friends/links", methods=["POST"])
@login_required
def friend_link_create():
    note = " ".join((request.form.get("note") or "").split())[:60]
    max_uses = _int(request.form.get("max_uses"), 0)
    days = _int(request.form.get("days"), -1)
    if not 1 <= max_uses <= 100 or not 0 <= days <= 365:
        flash("Valori nevalide: 1–100 utilizări, 0–365 zile.", "error")
    elif db_social.count_rows("friend_links", "user_id", g.user["id"]) >= MAX_FRIEND_LINKS:
        flash(f"Poți avea cel mult {MAX_FRIEND_LINKS} de linkuri. Șterge unul vechi.", "error")
    else:
        expires_at = int(time.time()) + days * 86400 if days else None
        db_social.create_friend_link(g.user["id"], secrets.token_urlsafe(12), note, max_uses,
                                     expires_at)
        flash("Link creat — copiază-l și trimite-l.", "ok")
    return redirect(url_for("social.friends") + "#links")


@bp.route("/friends/links/<int:link_id>/revoke", methods=["POST"])
@login_required
def friend_link_revoke(link_id):
    if db_social.revoke_friend_link(g.user["id"], link_id):
        flash("Link dezactivat.", "ok")
    return redirect(url_for("social.friends") + "#links")


@bp.route("/friends/links/<int:link_id>/delete", methods=["POST"])
@login_required
def friend_link_delete(link_id):
    if db_social.delete_friend_link(g.user["id"], link_id):
        flash("Link șters.", "ok")
    return redirect(url_for("social.friends") + "#links")


@bp.route("/f/<code>", methods=["GET", "POST"])
def friend_link(code):
    """Landing page of a friend link — works with or without an account."""
    code = normalize_friend_code(code)
    ip_key = f"flink-ip:{client_ip()}"
    if _limited(ip_key, LIMIT_FRIEND_LINK_IP):
        return render_template("friend_link.html", link=None, state="limited"), 429
    link = db_social.get_usable_friend_link(code) if code else None
    if link is None:
        db.record_attempt(ip_key)
        return render_template("friend_link.html", link=None, state="invalid"), 404

    if g.user is None:
        return render_template("friend_link.html", link=link, state="anonymous",
                               can_signup=friend_signup_enabled())
    if link["user_id"] == g.user["id"]:
        return render_template("friend_link.html", link=link, state="own")
    if db_social.are_friends(g.user["id"], link["user_id"]):
        return render_template("friend_link.html", link=link, state="already")
    if request.method == "POST":
        outcome = db_social.use_friend_link(code, g.user["id"])
        if outcome == "friends":
            flash(f"Tu și {link['display_name']} sunteți acum prieteni.", "ok")
            return redirect(url_for("social.friends"))
        if outcome == "already":
            return render_template("friend_link.html", link=link, state="already")
        db.record_attempt(ip_key)
        return render_template("friend_link.html", link=None, state="invalid"), 404
    return render_template("friend_link.html", link=link, state="confirm")


# ════════════════════════════════════════════ Common free time ══

@bp.route("/friends/free-time")
@login_required
def free_time():
    today = datetime.now().date()
    return render_template(
        "freetime.html",
        user=g.user,
        friends=db_social.list_friends(g.user["id"]),
        monday=(today - timedelta(days=today.weekday())).isoformat(),
        today=today.isoformat(),
        preselect=[normalize_username(u) for u in request.args.get("with", "").split(",") if u],
    )


@bp.route("/api/busy")
@login_required
def api_busy():
    """Busy intervals of me and some friends, plus the slots everyone has free.

    Query parameters: ``users`` (comma-separated usernames), ``from`` /
    ``to`` (ISO, max 14 days), ``me`` (``0`` to leave myself out),
    ``start`` / ``end`` (hours of the daily window, default 7–22) and
    ``min`` (shortest useful slot in minutes, default 30).
    """
    start, end, error = parse_range(request.args, max_days=14)
    if error:
        return jsonify({"error": error}), 400
    names = [normalize_username(u)[:64] for u in request.args.get("users", "").split(",")
             if u.strip()]
    if len(names) > MAX_BUSY_PEOPLE:
        return jsonify({"error": f"At most {MAX_BUSY_PEOPLE} friends"}), 400
    w0 = _int(request.args.get("start"), 7)
    w1 = _int(request.args.get("end"), 22)
    min_minutes = _int(request.args.get("min"), 30)
    if not (0 <= w0 < w1 <= 24) or not 15 <= min_minutes <= 600:
        return jsonify({"error": "Invalid window"}), 400

    me = g.user
    people = []
    if request.args.get("me", "1") != "0":
        people.append((me, Calendar(me, _default_week1()), Viewer.owner(), True))
    friend_ids = db_social.friend_ids(me["id"])
    for name in dict.fromkeys(names):
        other = db.get_user_by_username(name)
        if other is None or other["id"] not in friend_ids or not other["is_active"]:
            return jsonify({"error": f"Not a friend: {name}"}), 404
        people.append((other, Calendar(other, _default_week1()),
                       Viewer.friend(other["id"], me["id"]), False))

    window = (w0 * 60, min(w1 * 60, 23 * 60 + 59))
    out_people = []
    common: dict = {}
    d = start
    all_busy: dict = {}
    while d <= end:
        all_busy[d.isoformat()] = []
        d += timedelta(days=1)
    for user, cal, viewer, is_me in people:
        days = {}
        for iso in all_busy:
            busy = merge_intervals(cal.busy_intervals(date.fromisoformat(iso), viewer))
            days[iso] = [[s, e] for s, e in busy]
            all_busy[iso].extend(busy)
        out_people.append({"username": user["username"], "display_name": user["display_name"],
                           "me": is_me, "days": days})
    for iso, busy in all_busy.items():
        common[iso] = [{"start": s, "end": e, "start_hhmm": minutes_to_hhmm(s),
                        "end_hhmm": minutes_to_hhmm(e)}
                       for s, e in free_between(busy, window, min_minutes)]
    return jsonify({"from": start.isoformat(), "to": end.isoformat(), "window": list(window),
                    "people": out_people, "common": common})
