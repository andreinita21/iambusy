"""
Notification emails: friend requests, activity invites, answers to them and
changes to activities you go to.

* Every kind can be switched off — from the account page or with the
  one-click link at the bottom of each email (``users.email_notify``).
* Requests and invites carry an *action link*: a signed, personal token that
  opens a page with Accept / Decline buttons, usable without logging in.
  Opening it changes nothing (mail scanners follow links); the button does.
  Tokens expire after 14 days and stop working if the address changes.
* Limits keep anyone from flooding an inbox: per sender / activity and at
  most 30 notification emails a day per recipient.
"""

import hashlib
from typing import Iterable, Optional

from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

import db
import mailer
from auth import _limited, absolute_url

KIND_LABELS = db.EMAIL_KIND_LABELS
KINDS = tuple(KIND_LABELS)
ACTION_MAX_AGE = 14 * 24 * 3600
UNSUBSCRIBE_MAX_AGE = 365 * 24 * 3600
LIMIT_PER_RECIPIENT = (30, 24 * 3600)
LIMIT_SAME_EVENT = (1, 24 * 3600)      # the same request / invite / answer
LIMIT_CHANGES = (3, 60 * 60)           # edits to one activity, per participant


# ═══════════════════════════════════════════════════════ Tokens ══

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="iambusy-email-action")


def _email_mark(email: str) -> str:
    return hashlib.sha256((email or "").encode()).hexdigest()[:12]


def make_token(user: dict, kind: str, subject) -> str:
    return _serializer().dumps({"u": user["id"], "k": kind, "s": subject,
                                "e": _email_mark(user["email"])})


def read_token(token: str, kinds: tuple, max_age: int) -> tuple[Optional[dict], Optional[dict]]:
    """``(user, data)`` for a valid token of one of *kinds*, else ``(None, None)``."""
    try:
        data = _serializer().loads(token, max_age=max_age)
    except BadSignature:
        return None, None
    if not isinstance(data, dict) or data.get("k") not in kinds:
        return None, None
    user = db.get_user(data.get("u")) if isinstance(data.get("u"), int) else None
    if user is None or not user["is_active"] or not user["email"] \
            or data.get("e") != _email_mark(user["email"]):
        return None, None
    return user, data


# ═══════════════════════════════════════════════════════ Sending ══

def wants(user: Optional[dict], kind: str) -> bool:
    return bool(user and user["is_active"] and user["email"]
                and kind in (user["email_notify"] or "").split(","))


def _deliver(user: dict, kind: str, throttle_key: str, limit, subject: str,
             greeting_lines: list[str], button: str = "", url: str = "") -> bool:
    if not mailer.enabled() or not wants(user, kind):
        return False
    per_user = f"mail-to:{user['id']}"
    if _limited(throttle_key, limit) or _limited(per_user, LIMIT_PER_RECIPIENT):
        return False
    db.record_attempt(throttle_key, per_user)
    unsub = absolute_url(url_for("social.unsubscribe",
                                 token=make_token(user, "unsub", kind)))
    text, html = mailer.compose(
        f"Salut, {user['display_name']}!", greeting_lines, button, url,
        "Setările notificărilor sunt în pagina contului: " + absolute_url("/account#notifications"),
        unsubscribe=(KIND_LABELS[kind], unsub))
    mailer.send(user["email"], subject, text, html, unsubscribe_url=unsub)
    return True


def _action_url(user: dict, kind: str, subject: int) -> str:
    return absolute_url(url_for("social.email_action", token=make_token(user, kind, subject)))


def friend_request(sender: dict, recipient_id: int) -> None:
    user = db.get_user(recipient_id)
    if user is None:
        return
    _deliver(user, "friends", f"mail-fr:{sender['id']}:{user['id']}", LIMIT_SAME_EVENT,
             f"{sender['display_name']} vrea să fiți prieteni pe IamBusy",
             [f"{sender['display_name']} (@{sender['username']}) ți-a trimis o cerere de prietenie.",
              "Dacă o accepți, vă vedeți unul altuia programul — fiecare doar cât permite celuilalt."],
             "Răspunde la cerere", _action_url(user, "friend", sender["id"]))


def friend_accepted(accepter: dict, requester_id: int) -> None:
    user = db.get_user(requester_id)
    if user is None:
        return
    _deliver(user, "answers", f"mail-fa:{accepter['id']}:{user['id']}", LIMIT_SAME_EVENT,
             f"{accepter['display_name']} ți-a acceptat cererea de prietenie",
             [f"{accepter['display_name']} (@{accepter['username']}) a acceptat cererea ta. "
              "De acum vă vedeți programul, atât cât vă permiteți unul altuia."],
             "Vezi programul", absolute_url(f"/friends/{accepter['username']}"))


def activity_invites(organiser: dict, activity: dict, when: str,
                     user_ids: Iterable[int]) -> None:
    for uid in user_ids:
        user = db.get_user(uid)
        if user is None:
            continue
        _deliver(user, "invites", f"mail-inv:{activity['id']}:{uid}", LIMIT_SAME_EVENT,
                 f"{organiser['display_name']} te invită: {activity['title']}",
                 [f"{organiser['display_name']} (@{organiser['username']}) te invită la "
                  f"„{activity['title']}”.", when,
                  "Dacă accepți, apare și în calendarul tău, iar prietenii tăi văd că ești ocupat."],
                 "Răspunde la invitație", _action_url(user, "invite", activity["id"]))


def invite_answer(participant: dict, organiser_id: int, activity: dict, outcome: str) -> None:
    """*outcome*: ``accepted``, ``declined`` or ``left``."""
    user = db.get_user(organiser_id)
    if user is None:
        return
    name = participant["display_name"]
    text = {"accepted": f"{name} vine la „{activity['title']}”.",
            "declined": f"{name} a refuzat invitația la „{activity['title']}”.",
            "left": f"{name} a renunțat la „{activity['title']}”."}[outcome]
    _deliver(user, "answers", f"mail-ans:{activity['id']}:{participant['id']}:{outcome}",
             LIMIT_SAME_EVENT, text, [text], "Deschide editorul", absolute_url("/manage"))


def activity_changed(organiser: dict, activity: dict, when: str, user_ids: Iterable[int],
                     cancelled: bool = False) -> None:
    for uid in user_ids:
        user = db.get_user(uid)
        if user is None:
            continue
        if cancelled:
            subject = f"Anulat: {activity['title']}"
            lines = [f"{organiser['display_name']} a anulat „{activity['title']}” ({when}).",
                     "A dispărut și din calendarul tău."]
            button, url = "Deschide IamBusy", absolute_url("/")
        else:
            subject = f"Modificat: {activity['title']}"
            lines = [f"{organiser['display_name']} a modificat „{activity['title']}”.",
                     f"Acum: {when}."]
            button, url = "Vezi calendarul", absolute_url("/")
        _deliver(user, "changes", f"mail-chg:{activity['id']}:{uid}", LIMIT_CHANGES,
                 subject, lines, button, url)


def answer_outcome(previous_status: str, accept: bool) -> Optional[str]:
    """What an RSVP changed, for :func:`invite_answer` (``None``: nothing new)."""
    if accept:
        return None if previous_status == "accepted" else "accepted"
    if previous_status == "declined":
        return None
    return "left" if previous_status == "accepted" else "declined"


def after_answer(participant: dict, act: Optional[dict], accept: bool) -> None:
    """Email the organiser about an RSVP (*act*: the participation before it)."""
    if act is None:
        return
    outcome = answer_outcome(act["status"], accept)
    if outcome:
        invite_answer(participant, act["owner_id"], act, outcome)
