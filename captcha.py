"""
Cloudflare Turnstile on the forms bots like most: sign-up and "forgot
password" (both send email, so a bot could otherwise spend the daily quota
or mail strangers).  Off until ``TURNSTILE_SITE_KEY`` / ``TURNSTILE_SECRET_KEY``
are set; then the widget is shown and every POST is checked server-side.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("iambusy.captcha")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
SCRIPT_URL = "https://challenges.cloudflare.com/turnstile/v0/api.js"
ORIGIN = "https://challenges.cloudflare.com"

_settings = {"site_key": "", "secret": ""}


def configure(*, site_key: str, secret: str) -> None:
    _settings.update(site_key=(site_key or "").strip(), secret=(secret or "").strip())


def enabled() -> bool:
    return bool(_settings["site_key"] and _settings["secret"])


def site_key() -> str:
    return _settings["site_key"] if enabled() else ""


def verify(token, remote_ip: str = "") -> bool:
    """``True`` when Turnstile is off or Cloudflare vouches for *token*."""
    if not enabled():
        return True
    if not token or len(token) > 2048:
        return False
    data = urllib.parse.urlencode({"secret": _settings["secret"], "response": token,
                                   "remoteip": remote_ip}).encode()
    request = urllib.request.Request(VERIFY_URL, data=data, method="POST",
                                     headers={"user-agent": "IamBusy/1.0 (+https://www.iambusy.ro)"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return bool(json.load(response).get("success"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Fail closed: without Cloudflare the site isn't reachable anyway.
        log.error("Turnstile check failed: %s", exc)
        return False
