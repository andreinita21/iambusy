"""
Colour themes: every account can pick how the site looks for them.

* A theme is 14 colours (``KEYS``); the in-between shades (second background,
  second text, hairlines…) are derived from them, so a theme made from
  scratch always looks finished.
* Presets are inspired by well-known terminal / editor themes.  Any of them
  can be copied into the editor, changed and saved as the user's own.
* The choice is stored on the account (``users.theme``) and rendered as a
  small ``<style>`` block in every page's ``<head>`` — no flash of the
  default colours, and it follows the user to every device.
"""

import json
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup

import db
from auth import login_required

bp = Blueprint("themes", __name__)

MAX_THEMES = 12
NAME_MAX = 40
HEX_RE = re.compile(r"^#[0-9a-f]{6}$")

# (key, label, what it colours) — the order of the editor.
KEYS = (
    ("paper", "Fundal", "fundalul paginii"),
    ("card", "Carduri", "panouri, butoane, câmpuri"),
    ("ink", "Text", "textul principal"),
    ("line", "Contururi", "chenarele și umbrele"),
    ("muted", "Text secundar", "detalii, explicații"),
    ("on_accent", "Text pe culori", "scrisul de pe butoanele și etichetele colorate"),
    ("yellow", "Principal", "butoanele principale, ziua de azi, sigla"),
    ("pink", "Ocupat", "starea „ocupat” și categoria roz"),
    ("lime", "Liber", "starea „liber” și categoria verde"),
    ("blue", "Albastru", "categoria albastră (ex. Curs)"),
    ("orange", "Portocaliu", "categoria portocalie (ex. Seminar)"),
    ("purple", "Mov", "categoria mov (ex. Laborator)"),
    ("cyan", "Cyan", "categoria cyan (ex. Altele)"),
    ("red", "Roșu", "ștergere, erori, „ocupat” la prieteni"),
)
KEY_NAMES = tuple(k for k, _, _ in KEYS)


def _p(paper, card, ink, muted, on_accent, yellow, pink, lime, blue, orange, purple, cyan, red,
       line=None, **derived):
    colors = dict(paper=paper, card=card, ink=ink, line=line or ink, muted=muted,
                  on_accent=on_accent, yellow=yellow, pink=pink, lime=lime, blue=blue,
                  orange=orange, purple=purple, cyan=cyan, red=red)
    return {"colors": colors, "derived": derived}


# key → (name, palette).  Accents are chosen light enough for dark text on them.
PRESETS = {
    "iambusy-dark": ("IamBusy întunecat", _p(
        "#17171c", "#262630", "#f5f2ea", "#a39f94", "#111111", "#ffd23f", "#ff7bb0", "#b9f06b",
        "#79aaff", "#ffa552", "#c9a7ff", "#7ee3ff", "#ff6b6b",
        paper_2="#202028", card_2="#1f1f27", ink_2="#d9d5ca")),
    "iambusy-light": ("IamBusy luminos", _p(
        "#f3efe4", "#ffffff", "#111111", "#6f6a60", "#111111", "#ffd23f", "#ff7bb0", "#b9f06b",
        "#79aaff", "#ffa552", "#c9a7ff", "#7ee3ff", "#ff6b6b",
        paper_2="#e9e3d3", card_2="#f8f5ec", ink_2="#3d3d3d")),
    "catppuccin-mocha": ("Catppuccin Mocha", _p(
        "#1e1e2e", "#313244", "#cdd6f4", "#9399b2", "#11111b", "#f9e2af", "#f5c2e7", "#a6e3a1",
        "#89b4fa", "#fab387", "#cba6f7", "#89dceb", "#f38ba8", card_2="#262637")),
    "catppuccin-latte": ("Catppuccin Latte", _p(
        "#e6e9ef", "#eff1f5", "#4c4f69", "#6c6f85", "#1e1e2e", "#f0c26b", "#f2a5dd", "#8fd18a",
        "#8db0f9", "#fca474", "#c3a0f7", "#7fd0ee", "#ee7d93")),
    "dracula": ("Dracula", _p(
        "#282a36", "#383a4a", "#f8f8f2", "#a4acd6", "#21222c", "#f1fa8c", "#ff79c6", "#50fa7b",
        "#9aaefc", "#ffb86c", "#bd93f9", "#8be9fd", "#ff5555")),
    "nord": ("Nord", _p(
        "#2e3440", "#3b4252", "#eceff4", "#a7b1c4", "#2e3440", "#ebcb8b", "#e0a4c4", "#a3be8c",
        "#81a1c1", "#d08770", "#b48ead", "#88c0d0", "#d57780")),
    "gruvbox": ("Gruvbox", _p(
        "#282828", "#3c3836", "#ebdbb2", "#a89984", "#1d2021", "#fabd2f", "#e78a9e", "#b8bb26",
        "#83a598", "#fe8019", "#d3869b", "#8ec07c", "#fb4934")),
    "tokyo-night": ("Tokyo Night", _p(
        "#1a1b26", "#24283b", "#c0caf5", "#8189b5", "#15161e", "#e0af68", "#f4a3c8", "#9ece6a",
        "#7aa2f7", "#ff9e64", "#bb9af7", "#7dcfff", "#f7768e")),
    "rose-pine": ("Rosé Pine", _p(
        "#191724", "#26233a", "#e0def4", "#908caa", "#191724", "#f6c177", "#ebbcba", "#a8d8a0",
        "#8fb3e8", "#f2a97a", "#c4a7e7", "#9ccfd8", "#eb6f92")),
    "one-dark": ("One Dark", _p(
        "#282c34", "#323842", "#dcdfe4", "#8b919c", "#1e2127", "#e5c07b", "#e6a0c9", "#98c379",
        "#61afef", "#d19a66", "#c678dd", "#56b6c2", "#e06c75")),
    "solarized-light": ("Solarized Light", _p(
        "#eee8d5", "#fdf6e3", "#073642", "#586e75", "#002b36", "#e3c35a", "#ec8fbb", "#b9c75a",
        "#7fb7e6", "#ef9a6a", "#a9ace6", "#7ccbc4", "#f07a77")),
    "everforest": ("Everforest", _p(
        "#2d353b", "#3a464c", "#d3c6aa", "#9da9a0", "#232a2e", "#dbbc7f", "#e3a1b5", "#a7c080",
        "#7fbbb3", "#e69875", "#d699b6", "#83c092", "#e67e80")),
    "bubblegum": ("Bubblegum (tot roz)", _p(
        "#ffe4f1", "#fff5fa", "#5a0f36", "#9c4f76", "#4a0a2c", "#ff9ccf", "#ff5fa8", "#ffd0e7",
        "#d9a6ff", "#ffa3c4", "#e7a3f0", "#f7b3d9", "#ff4d8d")),
    "amethyst": ("Ametist (tot mov)", _p(
        "#160e22", "#251838", "#f1e6ff", "#b39cd4", "#1a0b2e", "#c084fc", "#af99ff", "#ddd6fe",
        "#8f9bfb", "#f5a3e0", "#e879f9", "#bcc6ff", "#f7609c", line="#7c4dff",
        paper_2="#1e1430", card_2="#1d1330", ink_2="#d8c8f0")),
}
DEFAULT_PRESET = "iambusy-dark"


# ═══════════════════════════════════════════════════════ Colours ══

def _rgb(hex_color: str) -> tuple:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """*a* moved a fraction *t* of the way towards *b*."""
    return _hex(x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b)))


def luminance(hex_color: str) -> float:
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g_, b = (channel(c) for c in _rgb(hex_color))
    return 0.2126 * r + 0.7152 * g_ + 0.0722 * b


def clean_colors(raw) -> dict | None:
    """All colours as lowercase ``#rrggbb``, or ``None`` if any is missing / invalid.

    ``line`` came later: themes saved before it existed outline in the text colour.
    """
    if not isinstance(raw, dict):
        return None
    out = {}
    for key in KEY_NAMES:
        value = str(raw.get(key) or (raw.get("ink") if key == "line" else "") or "").strip().lower()
        if not HEX_RE.match(value):
            return None
        out[key] = value
    return out


def css_vars(colors: dict, derived: dict = None) -> dict:
    """Every CSS custom property a palette sets (``static/style.css`` tokens)."""
    derived = derived or {}
    c = colors
    dark = luminance(c["paper"]) < 0.4
    r, g_, b = _rgb(c["ink"])
    return {
        "color-scheme": "dark" if dark else "light",
        "--paper": c["paper"],
        "--paper-2": derived.get("paper_2") or mix(c["paper"], c["ink"], 0.06),
        "--card": c["card"],
        "--card-2": derived.get("card_2") or mix(c["card"], c["paper"], 0.5),
        "--ink": c["ink"],
        "--ink-2": derived.get("ink_2") or mix(c["ink"], c["paper"], 0.14),
        "--line": c.get("line") or c["ink"],
        "--muted": c["muted"],
        "--line-soft": f"rgba({r}, {g_}, {b}, {0.13 if dark else 0.14})",
        "--on-accent": c["on_accent"],
        "--on-accent-inv": "#ffffff" if luminance(c["on_accent"]) < 0.4 else "#111111",
        **{f"--{k}": c[k] for k in ("yellow", "pink", "lime", "cyan", "orange", "purple", "red",
                                    "blue")},
    }


def stylesheet(colors: dict, derived: dict = None) -> str:
    body = "".join(f"{k}:{v};" for k, v in css_vars(colors, derived).items())
    # Beats both the default tokens and the light / dark toggle's [data-theme].
    return f"html:root,html:root[data-theme]{{{body}}}"


# ════════════════════════════════════════════════ Active theme ══

def resolve(user) -> tuple[str, str, dict | None, dict]:
    """``(key, name, colors, derived)`` of *user*'s theme; ``colors`` is ``None`` for the default."""
    key = (user["theme"] if user and "theme" in user.keys() else "") or ""
    if key.startswith("preset:") and key[7:] in PRESETS:
        name, palette = PRESETS[key[7:]]
        return key, name, palette["colors"], palette["derived"]
    if key.startswith("custom:") and key[7:].isdigit():
        row = db.get_user_theme(user["id"], int(key[7:]))
        colors = clean_colors(json.loads(row["colors"])) if row else None
        if colors:
            return key, row["name"], colors, {}
    return "", "", None, {}


def theme_style() -> Markup:
    """The ``<style>`` for the signed-in user's theme (empty for the default look)."""
    if "theme_style" not in g:
        _, _, colors, derived = resolve(g.get("user"))
        g.theme_style = Markup(f'<style id="user-theme">{stylesheet(colors, derived)}</style>'
                               if colors else "")
    return g.theme_style


# ═══════════════════════════════════════════════════════ Routes ══

def _page_data(user) -> dict:
    custom = []
    for row in db.list_user_themes(user["id"]):
        colors = clean_colors(json.loads(row["colors"]))
        if colors:
            custom.append({"key": f"custom:{row['id']}", "id": row["id"], "name": row["name"],
                           "colors": colors})
    presets = [{"key": f"preset:{k}", "name": name, "colors": p["colors"], "derived": p["derived"]}
               for k, (name, p) in PRESETS.items()]
    return {"presets": presets, "custom": custom,
            "keys": [{"key": k, "label": label, "hint": hint} for k, label, hint in KEYS]}


@bp.route("/account/theme")
@login_required
def theme_page():
    data = _page_data(g.user)
    active, _, _, _ = resolve(g.user)
    return render_template("theme.html", user=g.user, data=data, active=active,
                           max_themes=MAX_THEMES)


def _back(anchor: str = "") -> str:
    return url_for("themes.theme_page") + anchor


@bp.route("/account/theme/use", methods=["POST"])
@login_required
def use_theme():
    key = request.form.get("theme") or ""
    if key == "" or key == f"preset:{DEFAULT_PRESET}":
        db.update_user(g.user["id"], theme="")
    elif key.startswith("preset:") and key[7:] in PRESETS:
        db.update_user(g.user["id"], theme=key)
    elif key.startswith("custom:") and key[7:].isdigit() \
            and db.get_user_theme(g.user["id"], int(key[7:])):
        db.update_user(g.user["id"], theme=key)
    else:
        flash("Tema aceasta nu există.", "error")
        return redirect(_back())
    flash("Tema a fost schimbată.", "ok")
    return redirect(_back())


@bp.route("/account/theme/save", methods=["POST"])
@login_required
def save_theme():
    user = g.user
    name = " ".join((request.form.get("name") or "").split())[:NAME_MAX]
    colors = clean_colors({k: request.form.get(f"c_{k}") for k in KEY_NAMES})
    theme_id = request.form.get("id") or ""
    theme_id = int(theme_id) if theme_id.isdigit() else None
    if not name:
        flash("Dă-i un nume temei.", "error")
    elif colors is None:
        flash("Culorile trebuie să fie de forma #rrggbb.", "error")
    elif theme_id is None and len(db.list_user_themes(user["id"])) >= MAX_THEMES:
        flash(f"Poți păstra cel mult {MAX_THEMES} teme. Șterge una ca să faci loc.", "error")
    else:
        saved = db.save_user_theme(user["id"], name, json.dumps(colors), theme_id)
        if saved is None:
            flash("Tema aceasta nu există.", "error")
        else:
            if request.form.get("apply") == "1":
                db.update_user(user["id"], theme=f"custom:{saved}")
                flash(f"Tema „{name}” a fost salvată și e activă.", "ok")
            else:
                flash(f"Tema „{name}” a fost salvată.", "ok")
            return redirect(_back("#mine"))
    return redirect(_back("#editor"))


@bp.route("/account/theme/<int:theme_id>/delete", methods=["POST"])
@login_required
def delete_theme(theme_id):
    if db.delete_user_theme(g.user["id"], theme_id):
        flash("Tema a fost ștearsă.", "ok")
    else:
        flash("Tema aceasta nu există.", "error")
    return redirect(_back("#mine"))
