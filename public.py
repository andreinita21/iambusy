"""
The public face of IamBusy — everything a visitor without an account (or a
search engine, or a chat app building a link preview) can reach.

* ``canonical_redirect`` sends every request to the one public address
  (``SITE_URL``): other host names and plain http get a permanent redirect.
  Requests by IP address or ``localhost`` are left alone, so the app still
  works on the LAN and in development.
* Home page for visitors, privacy policy, terms.
* ``robots.txt``, ``sitemap.xml``, the web app manifest and ``favicon.ico``.
* Branded error pages (JSON for ``/api/`` routes).
"""

import hashlib
import ipaddress
import json
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from flask import (
    Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request,
    send_from_directory,
)
from werkzeug.exceptions import HTTPException

from auth import from_trusted_proxy

bp = Blueprint("public", __name__)

# Date the privacy policy / terms were last changed — shown on the pages and
# used as <lastmod> in the sitemap.  Bump it whenever their text changes.
LEGAL_UPDATED = date(2026, 10, 30)

SITE_NAME = "IamBusy"
SITE_DESCRIPTION = ("Orarul tău săptămânal în timp real: vezi dintr-o privire când ești liber, "
                    "împarte-l cu prietenii exact cât vrei și trimite un link oricui, fără cont.")
THEME_COLOR = "#17171c"

# Responses browsers and Cloudflare may keep (everything else is no-store).
CACHEABLE_ENDPOINTS = {"public.robots", "public.sitemap", "public.manifest", "public.favicon",
                       "public.security_txt"}

_settings = {"site_url": "", "host": "", "scheme": "", "operator": "", "contact": "",
             "session_days": 30, "short_session_hours": 12}


def configure(*, site_url: str, operator_name: str, contact_email: str,
              session_days: int = 30, short_session_hours: int = 12) -> None:
    site_url = (site_url or "").strip().rstrip("/")
    parts = urlsplit(site_url) if site_url else None
    if parts and (parts.scheme not in ("http", "https") or not parts.hostname or parts.path):
        raise SystemExit(f"SITE_URL must look like https://www.example.com, not {site_url!r}")
    _settings.update(
        site_url=site_url,
        host=parts.netloc.lower() if parts else "",
        scheme=parts.scheme if parts else "",
        operator=(operator_name or "").strip(),
        contact=(contact_email or "").strip(),
        session_days=session_days,
        short_session_hours=short_session_hours,
    )


def site_url() -> str:
    """Public base URL without a trailing slash (the request's own when unset)."""
    return _settings["site_url"] or request.host_url.rstrip("/")


def template_globals() -> dict:
    """``site`` in every template: base URL and who runs the site."""
    return {"site": {
        "url": site_url(),
        "name": SITE_NAME,
        "operator": _settings["operator"],
        "contact": _settings["contact"],
        "legal_updated": LEGAL_UPDATED,
        "session_days": _settings["session_days"],
        "short_session_hours": _settings["short_session_hours"],
    }}


_asset_versions: dict = {}


def asset_url(name: str) -> str:
    """``/static/<name>?v=<content hash>`` — browsers and Cloudflare keep static
    files for hours, so a changed file must get a new URL to be fetched again."""
    version = _asset_versions.get(name)
    if version is None:
        path = os.path.join(current_app.static_folder, name)
        try:
            with open(path, "rb") as fh:
                version = hashlib.sha256(fh.read()).hexdigest()[:10]
        except OSError:
            version = ""
        if not current_app.debug:
            _asset_versions[name] = version
    return f"/static/{name}?v={version}" if version else f"/static/{name}"


# ═════════════════════════════════════════════ Canonical host ══

def _is_local_host(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    name = name.strip("[]").lower()
    if name == "localhost" or name.endswith(".local") or "." not in name:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _visitor_scheme() -> str:
    """Scheme the visitor used — the proxy's word counts only from a trusted proxy."""
    if from_trusted_proxy():
        proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if proto in ("http", "https"):
            return proto
    return request.scheme


def canonical_redirect():
    """``before_request`` hook: one public address, always over https."""
    if not _settings["host"]:
        return None
    host = request.host.lower()
    if _is_local_host(host):
        return None
    if host == _settings["host"] and _visitor_scheme() == _settings["scheme"]:
        return None
    target = _settings["site_url"] + quote(request.path, safe="/:@!$&'()*+,;=-._~")
    if request.query_string:
        target += "?" + request.query_string.decode("latin-1")
    # 308 keeps the method and body of a form submission.
    return redirect(target, 301 if request.method in ("GET", "HEAD") else 308)


# ═══════════════════════════════════════════════════════ Pages ══

def landing():
    """Home page for visitors who are not logged in (``/`` renders it)."""
    return render_template("landing.html")


@bp.route("/confidentialitate")
def privacy_policy():
    return render_template("legal_privacy.html")


@bp.route("/termeni")
def terms():
    return render_template("legal_terms.html")


# ═══════════════════════════════════════════ Crawlers & icons ══

@bp.route("/robots.txt")
def robots():
    body = "\n".join([
        "User-agent: *",
        # Share and friend links stay crawlable on purpose: link-preview bots
        # honour robots.txt, and the pages themselves say noindex.
        "Disallow: /api/",
        "Disallow: /admin",
        "",
        f"Sitemap: {site_url()}/sitemap.xml",
        "",
    ])
    return Response(body, mimetype="text/plain")


@bp.route("/.well-known/security.txt")
def security_txt():
    """Where to report a vulnerability (RFC 9116) — only when a contact is set."""
    if not _settings["contact"]:
        abort(404)
    expires = (datetime.now(timezone.utc) + timedelta(days=180)).replace(microsecond=0)
    body = "\n".join([
        f"Contact: mailto:{_settings['contact']}",
        f"Expires: {expires.isoformat().replace('+00:00', 'Z')}",
        "Preferred-Languages: ro, en",
        f"Canonical: {site_url()}/.well-known/security.txt",
        "",
    ])
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    pages = [("/", None), ("/confidentialitate", LEGAL_UPDATED), ("/termeni", LEGAL_UPDATED)]
    base = site_url()
    entries = "".join(
        f"  <url><loc>{base}{path}</loc>"
        + (f"<lastmod>{updated.isoformat()}</lastmod>" if updated else "")
        + "</url>\n"
        for path, updated in pages
    )
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{entries}</urlset>\n")
    return Response(body, mimetype="application/xml")


@bp.route("/manifest.webmanifest")
def manifest():
    icons = [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
        {"src": "/static/icons/icon.svg", "sizes": "any", "type": "image/svg+xml"},
    ]
    body = {
        "name": SITE_NAME,
        "short_name": SITE_NAME,
        "description": SITE_DESCRIPTION,
        "lang": "ro",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOR,
        "theme_color": THEME_COLOR,
        "icons": icons,
    }
    return Response(json.dumps(body, ensure_ascii=False), mimetype="application/manifest+json")


@bp.route("/favicon.ico")
def favicon():
    return send_from_directory(current_app.static_folder, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


def cache_public(response):
    """Long-ish caching for the files above (``after_request`` helper)."""
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


# ════════════════════════════════════════════════ Error pages ══

ERRORS = {
    400: ("Cerere greșită", "Ceva din cerere nu are sens pentru noi. Întoarce-te și încearcă din nou."),
    403: ("Acces interzis", "Nu ai voie să vezi pagina asta."),
    404: ("Pagina nu există", "Linkul e greșit sau pagina a fost mutată ori ștearsă."),
    405: ("Metodă nepermisă", "Pagina asta nu se deschide așa."),
    413: ("Prea mare", "Ai trimis mai multe date decât acceptăm."),
    429: ("Prea multe cereri", "Ia o pauză scurtă și încearcă din nou."),
    500: ("A apărut o eroare", "Ceva n-a mers la noi. Încearcă din nou peste câteva momente."),
}


def handle_http_error(exc: HTTPException):
    code = exc.code if exc.code in ERRORS else 500
    title, message = ERRORS[code]
    if request.path.startswith("/api/"):
        return jsonify({"error": title}), code
    response = Response(render_template("error.html", code=code, title=title, message=message),
                        status=code, mimetype="text/html")
    if code == 405 and exc.valid_methods:
        response.headers["Allow"] = ", ".join(exc.valid_methods)
    return response


def handle_unexpected_error(exc: Exception):
    current_app.logger.exception("Unhandled error on %s", request.path)
    title, message = ERRORS[500]
    if request.path.startswith("/api/"):
        return jsonify({"error": title}), 500
    try:
        return render_template("error.html", code=500, title=title, message=message), 500
    except Exception:  # the template itself failed — fall back to plain text
        return Response(f"500 — {message}", status=500, mimetype="text/plain")
