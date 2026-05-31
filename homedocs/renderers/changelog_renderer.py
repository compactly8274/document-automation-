from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from homedocs.models import ChangelogEvent, Host


def _week_start(dt: datetime) -> datetime:
    local = dt.astimezone(timezone.utc)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


_MD_META = str.maketrans({
    "|": "\\|",
    "\n": " ",
    "\r": "",
    "`": "\\`",
    "*": "\\*",
    "_": "\\_",
    "[": "\\[",
    "]": "\\]",
    "<": "\\<",
    ">": "\\>",
})


def _esc_md(text: str) -> str:
    """Escape markdown metacharacters for safe use in changelog lines."""
    return text.translate(_MD_META)


def _fmt_event(ev: ChangelogEvent) -> str:
    ts = ev.timestamp.astimezone(timezone.utc).strftime("%H:%M UTC")
    host_tag = f"**[{ev.host.value}]**" if ev.host else "**[—]**"
    source_tag = " `[manual]`" if ev.source == "manual" else ""

    if ev.source == "manual":
        return f"- {ts} {host_tag}{source_tag} {_esc_md(ev.message or '')}"

    name = f"`{ev.container_name}`" if ev.container_name else ""
    stack = f" (stack: `{ev.stack_name}`)" if ev.stack_name else ""

    if ev.event_type == "updated" and ev.old_image_tag and ev.new_image_tag:
        return f"- {ts} {host_tag} {name} updated `{ev.old_image_tag}` → `{ev.new_image_tag}`{stack}"
    if ev.event_type == "updated":
        return f"- {ts} {host_tag} {name} restarted / redeployed{stack}"
    if ev.event_type == "created":
        return f"- {ts} {host_tag} {name} created{stack}"
    if ev.event_type == "destroyed":
        return f"- {ts} {host_tag} {name} destroyed{stack}"
    if ev.event_type == "pulled":
        tag = f" (`{ev.new_image_tag}`)" if ev.new_image_tag else ""
        return f"- {ts} {host_tag} {name} image pulled{tag}{stack}"

    msg = ev.message or ev.event_type
    return f"- {ts} {host_tag} {name} {msg}{stack}"


def render_changelog_md(
    events: list[ChangelogEvent],
    generated_at: Optional[datetime] = None,
) -> str:
    now = generated_at or datetime.now(timezone.utc)
    lines: list[str] = [
        "# Homelab Changelog",
        "",
        f"_Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    if not events:
        lines.append("_No events recorded yet._")
        lines.append("")
        return "\n".join(lines)

    # Sort events newest-first
    sorted_events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # Group by week
    weeks: dict[datetime, list[ChangelogEvent]] = {}
    for ev in sorted_events:
        ws = _week_start(ev.timestamp)
        weeks.setdefault(ws, []).append(ev)

    for week_start_dt in sorted(weeks.keys(), reverse=True):
        week_events = weeks[week_start_dt]
        lines.append(f"## Week of {week_start_dt.strftime('%Y-%m-%d')}")
        lines.append("")

        # Sub-group by day within the week
        days: dict[str, list[ChangelogEvent]] = {}
        for ev in week_events:
            day = ev.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
            days.setdefault(day, []).append(ev)

        for day in sorted(days.keys(), reverse=True):
            lines.append(f"### {day}")
            lines.append("")
            for ev in days[day]:
                lines.append(_fmt_event(ev))
            lines.append("")

    return "\n".join(lines)


def write_changelog(
    output_dir: str,
    events: list[ChangelogEvent],
    generated_at: Optional[datetime] = None,
):
    now = generated_at or datetime.now(timezone.utc)
    md = render_changelog_md(events, now)
    with open(os.path.join(output_dir, "changelog.md"), "w", encoding="utf-8") as f:
        f.write(md)
