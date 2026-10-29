"""
Calendar assembly — one user's activities, day by day, as a given viewer
is allowed to see them.

A :class:`Calendar` loads everything about its owner once (own activities,
accepted invites, categories, rules, participants); :meth:`Calendar.entries_on`
then expands a single date for a :class:`Viewer`:

* the owner sees everything;
* a friend sees what :func:`visibility.friend_level` allows — except that
  people invited to an activity always see it in full;
* a share link sees what :func:`visibility.link_level` allows.

"Busy" entries lose their title, place and category, and overlapping busy
entries are merged so their count doesn't leak either.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import db
import db_social
from schedule_engine import (
    BUSY_LABEL, DAY_DISPLAY, DAY_SHORT, DAYS, academic_week_number, build_day_timeline,
    busy_segments, compute_status, day_summary, find_next_activity, format_date_ro,
    free_windows, hhmm_to_minutes, is_odd_week, occurs_on, prepare_blocks_for_ui,
    relative_day_ro,
)
from visibility import friend_level, link_level


def parse_week1(raw, default: date) -> date:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Viewer:
    kind: str                       # 'self' | 'friend' | 'link'
    user_id: Optional[int] = None
    groups: tuple = ()
    preset: str = "busy"
    link_rules: dict = field(default_factory=dict)

    @classmethod
    def owner(cls) -> "Viewer":
        return cls("self")

    @classmethod
    def friend(cls, owner_id: int, viewer_id: int) -> "Viewer":
        return cls("friend", viewer_id, tuple(db_social.viewer_group_ids(owner_id, viewer_id)))

    @classmethod
    def link(cls, link: dict) -> "Viewer":
        return cls("link", preset=link["preset"], link_rules=db_social.share_rules(link["id"]))


class Calendar:
    def __init__(self, owner: dict, default_week1: date):
        self.owner = owner
        self.week1 = parse_week1(owner.get("week1_start"), default_week1)
        self.categories = {c["id"]: c for c in db.list_categories(owner["id"])}

        self.items: list[dict] = []
        for a in db.get_all_activities(owner["id"]):
            self.items.append({**a, "cat": a["category_id"], "week1": self.week1,
                               "shared": False, "creator_id": owner["id"]})
        for s in db_social.shared_activities(owner["id"], statuses=("accepted",)):
            self.items.append({
                **s, "cat": s["my_category_id"], "shared": True, "creator_id": s["owner_id"],
                # Weeks of a shared activity follow its creator's academic calendar.
                "week1": parse_week1(s["owner_week1_start"], default_week1),
            })

        self.participants = db_social.participants(i["id"] for i in self.items)
        self.cat_rules = db_social.category_rules(owner["id"])
        self.act_rules = db_social.activity_rules(owner["id"])
        self._insiders: dict[int, set] = {}
        self._with: dict[int, list] = {}
        for it in self.items:
            people = self.participants.get(it["id"], [])
            self._insiders[it["id"]] = {it["creator_id"]} | {p["user_id"] for p in people}
            names = [p["display_name"] for p in people
                     if p["status"] == "accepted" and p["user_id"] != owner["id"]]
            if it["shared"]:
                names.insert(0, it["owner_name"])
            self._with[it["id"]] = names

    # ── visibility ─────────────────────────────────────────────
    def level(self, item: dict, viewer: Viewer) -> str:
        if viewer.kind == "self":
            return "full"
        act_rules = self.act_rules.get(item["id"], {})
        cat_rules = self.cat_rules.get(item["cat"], {})
        if viewer.kind == "friend":
            if viewer.user_id in self._insiders.get(item["id"], ()):
                return "full"
            return friend_level(act_rules, cat_rules, viewer.user_id, viewer.groups)
        return link_level(viewer.preset, viewer.link_rules.get(item["cat"]), act_rules, cat_rules)

    def entries_on(self, d: date, viewer: Viewer) -> list[dict]:
        visible: list[dict] = []
        masked: list[dict] = []
        for it in self.items:
            if not occurs_on(it, d, it["week1"]):
                continue
            level = self.level(it, viewer)
            if level == "hidden":
                continue
            if level == "busy":
                masked.append({"title": BUSY_LABEL, "start": it["start_time"],
                               "end": it["end_time"], "masked": True})
                continue
            cat = self.categories.get(it["cat"])
            entry = {
                "title": it["title"], "start": it["start_time"], "end": it["end_time"],
                "color": cat["color"] if cat else "x", "category": cat["name"] if cat else "",
                "masked": False,
            }
            insider = viewer.kind == "self" or (
                viewer.kind == "friend" and viewer.user_id in self._insiders.get(it["id"], ()))
            if insider:
                entry["with"] = self._with.get(it["id"], [])
            if viewer.kind == "self":
                entry["id"] = it["id"]
                entry["shared"] = it["shared"]
            visible.append(entry)
        visible.extend(_merge_masked(masked))
        visible.sort(key=lambda e: (e["start"], e["end"]))
        return visible

    def busy_intervals(self, d: date, viewer: Viewer) -> list[tuple[int, int]]:
        return [(hhmm_to_minutes(e["start"]), hhmm_to_minutes(e["end"]))
                for e in self.entries_on(d, viewer)]


def _merge_masked(entries: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for e in sorted(entries, key=lambda e: e["start"]):
        if merged and e["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], e["end"])
        else:
            merged.append(dict(e))
    return merged


# ═══════════════════════════════════════════════ Date windows ══

class Window:
    """The dates a share link lets its viewer see (``None`` bounds = open)."""

    def __init__(self, first: Optional[date] = None, last: Optional[date] = None,
                 days: Optional[list[date]] = None):
        self.days = sorted(set(days)) if days else None
        self.first = self.days[0] if self.days else first
        self.last = self.days[-1] if self.days else last

    def allows(self, d: date) -> bool:
        if self.days is not None:
            return d in self.days
        return (self.first is None or d >= self.first) and (self.last is None or d <= self.last)

    def prev(self, d: date) -> Optional[date]:
        if self.days is not None:
            earlier = [x for x in self.days if x < d]
            return earlier[-1] if earlier else None
        p = d - timedelta(days=1)
        if self.last is not None and p > self.last:
            return self.last
        return p if self.allows(p) else None

    def next(self, d: date) -> Optional[date]:
        if self.days is not None:
            later = [x for x in self.days if x > d]
            return later[0] if later else None
        n = d + timedelta(days=1)
        if self.first is not None and n < self.first:
            return self.first
        return n if self.allows(n) else None

    def clamp(self, d: date) -> date:
        if self.allows(d):
            return d
        return self.next(d) or self.prev(d) or d


# ═════════════════════════════════════════════ View builders ══

def parse_range(args, max_days: int = 62):
    """``from`` / ``to`` query parameters → ``(start, end, error)``."""
    try:
        start = datetime.strptime(args.get("from", ""), "%Y-%m-%d").date()
        end = datetime.strptime(args.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        return None, None, "from/to must be YYYY-MM-DD"
    if end < start:
        return None, None, "to must be after from"
    if (end - start).days > max_days:
        return None, None, f"Range too large (max {max_days} days)"
    return start, end, None



def day_summaries(cal: Calendar, viewer: Viewer, start: date, end: date,
                  window: Optional[Window] = None) -> list[dict]:
    days = []
    d = start
    while d <= end:
        inside = window is None or window.allows(d)
        summary = day_summary(cal.entries_on(d, viewer) if inside else [], d, cal.week1)
        summary["out"] = not inside
        days.append(summary)
        d += timedelta(days=1)
    return days


def live_status(cal: Calendar, viewer: Viewer, now: datetime, name: str) -> dict:
    """``{"state": busy|free|off, "main", "sub", "until"}`` for right now."""
    today = now.date()
    timeline = build_day_timeline(cal.entries_on(today, viewer), today)
    next_act = find_next_activity(lambda d: cal.entries_on(d, viewer), today)
    main, sub, block = compute_status(now, timeline, name, next_activity=next_act)
    if block is None:
        state = "off"
    elif block["type"] == "course" and block["start_dt"] <= now < block["end_dt"]:
        state = "busy"
    else:
        state = "free"
    return {"state": state, "main": main, "sub": sub,
            "until": block["end_dt"].strftime("%H:%M") if block and state != "off" else ""}


def build_day_context(cal: Calendar, viewer: Viewer, view_date: date, now: datetime, *,
                      name: str, window: Optional[Window] = None,
                      show_status: bool = True) -> dict:
    """Everything ``index.html`` needs to render one day of *cal*."""
    today = now.date()
    week1 = cal.week1

    def entries_for(d: date) -> list[dict]:
        if window is not None and not window.allows(d):
            return []
        return cal.entries_on(d, viewer)

    in_window = window is None or window.allows(view_date)
    timeline = build_day_timeline(entries_for(view_date), view_date)
    next_act = find_next_activity(entries_for, view_date,
                                  until=window.last if window else None)

    week_is_odd = is_odd_week(week1, view_date)
    week_num = academic_week_number(week1, view_date)

    # ── status derivation ────────────────────────────────────
    is_today = view_date == today
    live = is_today and show_status and in_window
    if live:
        status_main, status_sub, current_block = compute_status(
            now, timeline, name, next_activity=next_act)
    else:
        n_courses = sum(1 for b in timeline if b["type"] == "course")
        status_main = f"Program pentru {relative_day_ro(view_date, today)}"
        if view_date.year != today.year:
            status_main += f" {view_date.year}"
        if not in_window:
            status_sub = "Ziua nu face parte din perioada partajată."
        else:
            status_sub = (
                "Nicio activitate programată." if n_courses == 0
                else f"{n_courses} {'activitate' if n_courses == 1 else 'activități'}."
            )
        current_block = None

    if current_block is None or not live:
        state = "off"
    elif current_block["type"] == "course" and current_block["start_dt"] <= now < current_block["end_dt"]:
        state = "busy"
    else:
        state = "free"

    # ── week label ───────────────────────────────────────────
    if week_num <= 0:
        week_label = f"Vacanță · începe pe {format_date_ro(week1, long=True)}"
    else:
        week_label = f"Săptămâna {week_num} · {'impară' if week_is_odd else 'pară'}"

    # ── week strip (Mon–Sun of the viewed week) ──────────────
    monday = view_date - timedelta(days=view_date.weekday())
    week_strip = []
    legend: dict[str, str] = {}
    for i in range(7):
        d = monday + timedelta(days=i)
        entries = entries_for(d)
        for e in entries:
            if not e.get("masked") and e.get("category"):
                legend.setdefault(e["category"], e["color"])
        week_strip.append({
            "short": DAY_SHORT[i],
            "day": d.day,
            "count": len(entries),
            "segments": busy_segments(entries),
            "link": f"?date={d.isoformat()}",
            "is_today": d == today,
            "is_selected": d == view_date,
            "is_weekend": i >= 5,
            "out": window is not None and not window.allows(d),
            "label": f"{DAY_DISPLAY[DAYS[i]]}, {format_date_ro(d, long=True)}",
        })

    # ── template context ─────────────────────────────────────
    blocks_for_ui = prepare_blocks_for_ui(timeline, current_block)
    if window is None:
        prev_date, next_date = view_date - timedelta(days=1), view_date + timedelta(days=1)
    else:
        prev_date, next_date = window.prev(view_date), window.next(view_date)
    n_courses = sum(1 for b in blocks_for_ui if b["type"] == "course")

    return {
        "user_name": name,
        "state": state,
        "is_today": is_today,
        "show_clock": is_today and show_status,
        "in_window": in_window,
        "has_courses": n_courses > 0,
        "n_courses": n_courses,
        "free_windows": free_windows(timeline) if in_window else [],
        "week_label": week_label,
        "week_num": week_num,
        "week_parity": "odd" if week_is_odd else "even",
        "day_key": DAYS[view_date.weekday()],
        "today_label": DAY_DISPLAY[DAYS[view_date.weekday()]],
        "view_date_str": format_date_ro(view_date, long=True),
        "view_date_iso": view_date.strftime("%Y-%m-%d"),
        "today_iso": today.strftime("%Y-%m-%d"),
        "now": now.strftime("%H:%M:%S"),
        "status_main": status_main,
        "status_sub": status_sub,
        "current_end_iso": current_block["end_dt"].isoformat() if (current_block and live) else "",
        "blocks": blocks_for_ui,
        "week_strip": week_strip,
        "legend": [{"name": k, "color": v} for k, v in legend.items()],
        "prev_link": f"?date={prev_date.isoformat()}" if prev_date else "",
        "next_link": f"?date={next_date.isoformat()}" if next_date else "",
        "window_first": window.first.isoformat() if window and window.first else "",
        "window_last": window.last.isoformat() if window and window.last else "",
    }

