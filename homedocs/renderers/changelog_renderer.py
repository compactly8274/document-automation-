from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from homedocs.models import ChangelogEvent, Host


def _week_start(dt: datetime) -> datetime:
    local = dt.astimezone(timezone.utc)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _fmt_single_event(ev: ChangelogEvent) -> str:
    ts = ev.timestamp.astimezone(timezone.utc).strftime("%H:%M UTC")
    host_tag = f"**[{ev.host.value}]**" if ev.host else "**[—]**"

    if ev.source == "manual":
        return f"- {ts} {host_tag} `[manual]` {ev.message}"

    name = f"`{ev.container_name}`" if ev.container_name else ""

    if ev.event_type == "updated" and ev.old_image_tag and ev.new_image_tag:
        return f"- {ts} {host_tag} {name} `{ev.old_image_tag}` → `{ev.new_image_tag}`"
    if ev.event_type == "updated":
        return f"- {ts} {host_tag} {name} redeployed"
    if ev.event_type == "created":
        tag = f" (`{ev.new_image_tag}`)" if ev.new_image_tag else ""
        return f"- {ts} {host_tag} {name} added{tag}"
    if ev.event_type == "destroyed":
        return f"- {ts} {host_tag} {name} removed"
    if ev.event_type == "status_changed" and ev.message:
        return f"- {ts} {host_tag} {name} {ev.message}"

    msg = ev.message or ev.event_type
    return f"- {ts} {host_tag} {name} {msg}"


def _fmt_stack_group(stack: str, host: Host, events: list[ChangelogEvent]) -> str:
    """Render multiple same-stack events as a single grouped entry."""
    ts = min(e.timestamp for e in events).astimezone(timezone.utc).strftime("%H:%M UTC")
    host_tag = f"**[{host.value}]**"
    lines = [f"- {ts} {host_tag} stack `{stack}` redeployed"]
    for ev in sorted(events, key=lambda e: e.container_name or ""):
        name = f"`{ev.container_name}`"
        if ev.old_image_tag and ev.new_image_tag:
            lines.append(f"  - {name} `{ev.old_image_tag}` → `{ev.new_image_tag}`")
        elif ev.event_type == "created":
            tag = f" (`{ev.new_image_tag}`)" if ev.new_image_tag else ""
            lines.append(f"  - {name} added{tag}")
        elif ev.event_type == "destroyed":
            lines.append(f"  - {name} removed")
        else:
            lines.append(f"  - {name} updated")
    return "\n".join(lines)


def _group_and_render_day(events: list[ChangelogEvent]) -> list[str]:
    """
    Render events for a single day.
    Image-changed/created/destroyed events sharing the same stack and occurring
    within 5 minutes of each other are collapsed into one stack-redeployment entry.
    Manual events and status changes are always individual.
    """
    STACK_WINDOW_SECONDS = 300

    # Separate manual/status events (always individual) from groupable ones
    individual = [e for e in events if e.source == "manual" or e.event_type == "status_changed"]
    groupable = [e for e in events if e.source != "manual" and e.event_type != "status_changed"]

    # Group groupable events by (host, stack) within a 5-minute window
    # Strategy: sort by time, then cluster events with same (host, stack) within window
    stack_buckets: dict[tuple, list[ChangelogEvent]] = defaultdict(list)
    ungrouped: list[ChangelogEvent] = []

    for ev in groupable:
        if ev.stack_name:
            key = (ev.host, ev.stack_name)
            stack_buckets[key].append(ev)
        else:
            ungrouped.append(ev)

    # A stack bucket only becomes a group if it has 2+ containers changed within the window
    rendered: list[tuple[datetime, str]] = []

    for (host, stack), bucket in stack_buckets.items():
        bucket.sort(key=lambda e: e.timestamp)
        # Split bucket into windows
        window: list[ChangelogEvent] = [bucket[0]]
        for ev in bucket[1:]:
            span = (ev.timestamp - window[0].timestamp).total_seconds()
            if span <= STACK_WINDOW_SECONDS:
                window.append(ev)
            else:
                # Flush current window
                if len(window) >= 2:
                    rendered.append((window[0].timestamp, _fmt_stack_group(stack, host, window)))
                else:
                    rendered.append((window[0].timestamp, _fmt_single_event(window[0])))
                window = [ev]
        # Flush last window
        if len(window) >= 2:
            rendered.append((window[0].timestamp, _fmt_stack_group(stack, host, window)))
        else:
            rendered.append((window[0].timestamp, _fmt_single_event(window[0])))

    for ev in ungrouped:
        rendered.append((ev.timestamp, _fmt_single_event(ev)))
    for ev in individual:
        rendered.append((ev.timestamp, _fmt_single_event(ev)))

    rendered.sort(key=lambda x: x[0], reverse=True)
    return [line for _, line in rendered]


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

    sorted_events = sorted(events, key=lambda e: e.timestamp, reverse=True)

    # Group by week → day
    weeks: dict[datetime, dict[str, list[ChangelogEvent]]] = {}
    for ev in sorted_events:
        ws = _week_start(ev.timestamp)
        day = ev.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
        weeks.setdefault(ws, {}).setdefault(day, []).append(ev)

    for week_dt in sorted(weeks.keys(), reverse=True):
        lines.append(f"## Week of {week_dt.strftime('%Y-%m-%d')}")
        lines.append("")
        for day in sorted(weeks[week_dt].keys(), reverse=True):
            lines.append(f"### {day}")
            lines.append("")
            for entry in _group_and_render_day(weeks[week_dt][day]):
                lines.append(entry)
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
