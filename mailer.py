"""
Outgoing email through Brevo's transactional API (https://api.brevo.com).

Only two kinds of messages exist: "confirm this address" and "reset your
password" (plus a short notice after a reset / address change).  Sending
happens on a background thread so that a page answers just as fast whether
or not an email went out — otherwise the response time would tell an
attacker which addresses have an account.
"""

import json
import logging
import threading
import urllib.error
import urllib.request

log = logging.getLogger("iambusy.mail")

API_URL = "https://api.brevo.com/v3/smtp/email"
USER_AGENT = "IamBusy/1.0 (+https://www.iambusy.ro)"

_settings = {"api_key": "", "sender": "", "sender_name": "IamBusy", "reply_to": "",
             "background": True}


def configure(*, api_key: str, sender: str, sender_name: str = "IamBusy",
              reply_to: str = "") -> None:
    _settings.update(api_key=(api_key or "").strip(), sender=(sender or "").strip(),
                     sender_name=sender_name or "IamBusy", reply_to=(reply_to or "").strip())


def enabled() -> bool:
    return bool(_settings["api_key"] and _settings["sender"])


def send(to: str, subject: str, text: str, html: str) -> None:
    """Queue one message; failures are logged, never raised to the visitor."""
    payload = {
        "sender": {"email": _settings["sender"], "name": _settings["sender_name"]},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html,
    }
    if _settings["reply_to"]:
        payload["replyTo"] = {"email": _settings["reply_to"]}
    if not enabled():
        log.warning("Email not configured (BREVO_API_KEY); dropped %r to %s", subject, to)
        return
    if _settings["background"]:
        threading.Thread(target=_deliver, args=(payload,), daemon=True).start()
    else:
        _deliver(payload)


def _deliver(payload: dict) -> bool:
    request = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"api-key": _settings["api_key"], "accept": "application/json",
                 "content-type": "application/json",
                 # Brevo's firewall turns away urllib's default User-Agent.
                 "user-agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.error("Brevo refused the email (%s): %s", exc.code, exc.read()[:300])
    except (urllib.error.URLError, TimeoutError) as exc:
        log.error("Could not reach Brevo: %s", exc)
    return False
