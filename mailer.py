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

from markupsafe import escape

import db

log = logging.getLogger("iambusy.mail")

API_URL = "https://api.brevo.com/v3/smtp/email"
USER_AGENT = "IamBusy/1.0 (+https://www.iambusy.ro)"

_settings = {"api_key": "", "sender": "", "sender_name": "IamBusy", "reply_to": "",
             "background": True}

# Brevo's free plan sends 300 a day; stopping short keeps a bot (or a bug) from
# spending all of it and keeps some room for the admin.
DAILY_LIMIT = 250


def configure(*, api_key: str, sender: str, sender_name: str = "IamBusy",
              reply_to: str = "") -> None:
    _settings.update(api_key=(api_key or "").strip(), sender=(sender or "").strip(),
                     sender_name=sender_name or "IamBusy", reply_to=(reply_to or "").strip())


def enabled() -> bool:
    return bool(_settings["api_key"] and _settings["sender"])


def compose(greeting: str, lines: list[str], button: str = "", url: str = "",
            footer: str = "", unsubscribe: tuple[str, str] = None) -> tuple[str, str]:
    """Plain-text and HTML bodies of one short email.

    *unsubscribe* is ``(what, url)`` for notification emails, e.g.
    ``("cereri de prietenie", "https://…/unsubscribe/…")``.
    """
    small = 'style="margin:0 0 14px;font-size:13px;color:#555"'
    text_parts = [greeting, *lines]
    if url:
        text_parts.append(f"{button}: {url}")
    if footer:
        text_parts.append(footer)
    text_parts.append("— IamBusy")
    paragraphs = "".join(f'<p style="margin:0 0 14px">{escape(l)}</p>' for l in lines)
    action = (f'<p style="margin:22px 0"><a href="{escape(url)}" style="display:inline-block;'
              'background:#ffd23f;color:#111;border:3px solid #111;border-radius:8px;'
              'padding:12px 20px;font-weight:700;text-decoration:none">'
              f'{escape(button)}</a></p><p {small}>Sau copiază linkul în browser:<br>'
              f'<span style="word-break:break-all">{escape(url)}</span></p>') if url else ""
    foot = f'<p {small}>{escape(footer)}</p>' if footer else ""
    unsub = ""
    if unsubscribe:
        what, unsub_url = unsubscribe
        text_parts.append(f"Nu mai vrei emailuri despre {what}? {unsub_url}")
        unsub = (f'<p style="margin:26px 0 0;font-size:12px;color:#777">Primești acest email pentru că '
                 f'ai cont pe IamBusy. <a href="{escape(unsub_url)}" style="color:#777">Nu mai vreau '
                 f'emailuri despre {escape(what)}</a>.</p>')
    html = ('<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:15px;'
            'line-height:1.5;color:#111;max-width:520px;margin:0 auto;padding:24px">'
            '<p style="margin:0 0 18px;font-size:20px;font-weight:700">IamBusy</p>'
            f'<p style="margin:0 0 14px">{escape(greeting)}</p>{paragraphs}{action}{foot}'
            f'<p style="margin:22px 0 0;font-size:13px;color:#555">— IamBusy</p>{unsub}</div>')
    return "\n\n".join(text_parts), html


def send(to: str, subject: str, text: str, html: str, unsubscribe_url: str = "") -> None:
    """Queue one message; failures are logged, never raised to the visitor."""
    payload = {
        "sender": {"email": _settings["sender"], "name": _settings["sender_name"]},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
        "htmlContent": html,
    }
    if unsubscribe_url:
        # RFC 8058 one-click unsubscribe, shown by Gmail / Outlook next to the sender.
        payload["headers"] = {"List-Unsubscribe": f"<{unsubscribe_url}>",
                              "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    if _settings["reply_to"]:
        payload["replyTo"] = {"email": _settings["reply_to"]}
    if not enabled():
        log.warning("Email not configured (BREVO_API_KEY); dropped %r to %s", subject, to)
        return
    if db.count_attempts("mail-global", 24 * 3600) >= DAILY_LIMIT:
        log.error("Daily email limit (%d) reached; dropped %r to %s", DAILY_LIMIT, subject, to)
        return
    db.record_attempt("mail-global")
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
