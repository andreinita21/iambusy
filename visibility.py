"""
Visibility rules — who sees what of someone's calendar.

Levels, from most open to most private:

* ``full``   — title, place, time
* ``busy``   — a red "Ocupat" block with no details
* ``hidden`` — not shown at all (the slot looks free)

For a friend looking at an activity, the first rule that matches wins:

1. activity × that friend
2. activity × the friend's groups   (several groups → the most private)
3. activity × all friends
4. category × that friend
5. category × the friend's groups   (several groups → the most private)
6. category × all friends
7. :data:`DEFAULT_LEVEL`

A public share link starts from its preset (or its own per-category
level) and per-activity "all friends" overrides can only make it more
private, never more open.

Rules are passed in as ``{(subject_type, subject_id): level}`` dicts, with
subject types ``"all"`` (id 0), ``"group"`` and ``"friend"``.
"""

from functools import reduce
from typing import Iterable, Optional

LEVELS = ("full", "busy", "hidden")
LEVEL_LABELS = {"full": "Complet", "busy": "Doar ocupat", "hidden": "Ascuns"}
DEFAULT_LEVEL = "busy"
_PRIVACY = {"full": 0, "busy": 1, "hidden": 2}

ALL = ("all", 0)


def more_private(a: str, b: str) -> str:
    return a if _PRIVACY[a] >= _PRIVACY[b] else b


def _match(rules: dict, viewer_id: int, viewer_groups: Iterable[int]) -> Optional[str]:
    if not rules:
        return None
    level = rules.get(("friend", viewer_id))
    if level:
        return level
    group_levels = [rules[("group", g)] for g in viewer_groups if ("group", g) in rules]
    if group_levels:
        return reduce(more_private, group_levels)
    return rules.get(ALL)


def friend_level(activity_rules: dict, category_rules: dict,
                 viewer_id: int, viewer_groups: Iterable[int]) -> str:
    """Level at which friend *viewer_id* sees one activity."""
    groups = tuple(viewer_groups)
    return (_match(activity_rules, viewer_id, groups)
            or _match(category_rules, viewer_id, groups)
            or DEFAULT_LEVEL)


def general_level(activity_rules: dict, category_rules: dict) -> str:
    """Level for "all friends" — no per-friend or group rule considered."""
    return (activity_rules or {}).get(ALL) or (category_rules or {}).get(ALL) or DEFAULT_LEVEL


def link_level(preset: str, link_category_level: Optional[str],
               activity_rules: dict, category_rules: dict) -> str:
    """Level at which a public share link shows one activity.

    *preset* is ``busy`` / ``full`` / ``friends`` ("as my friends see it");
    *link_category_level* is the link's own setting for the category, if any.
    """
    if link_category_level:
        base = link_category_level
    elif preset == "full":
        base = "full"
    elif preset == "friends":
        base = general_level(activity_rules, category_rules)
    else:
        base = "busy"
    override = (activity_rules or {}).get(ALL)
    return more_private(base, override) if override else base
