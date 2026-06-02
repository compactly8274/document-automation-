from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from homedocs.models import ChangelogEvent, ContainerRecord, Host


@dataclass
class ContainerDiff:
    change_type: str  # "added" | "removed" | "image_changed" | "status_changed"
    name: str
    host: Host
    compose_stack: Optional[str]
    old_tag: Optional[str] = None
    new_tag: Optional[str] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None


def diff_snapshots(
    old_snap: list[dict],
    new_containers: list[ContainerRecord],
) -> list[ContainerDiff]:
    """
    Compare a saved snapshot (list of dicts) against freshly collected containers.
    Returns only meaningful changes — transient status flaps during image updates
    are suppressed (image_changed takes precedence).
    """
    old_map: dict[tuple, dict] = {(d["host"], d["name"]): d for d in old_snap}
    new_map: dict[tuple, ContainerRecord] = {(c.host.value, c.name): c for c in new_containers}

    diffs: list[ContainerDiff] = []
    image_changed_keys: set[tuple] = set()

    # Image changes and additions/removals first pass
    for key, new_c in new_map.items():
        if key not in old_map:
            diffs.append(ContainerDiff(
                change_type="added",
                name=new_c.name,
                host=new_c.host,
                compose_stack=new_c.compose_stack,
                new_tag=new_c.tag,
                new_status=new_c.status,
            ))
        else:
            old = old_map[key]
            if old["tag"] != new_c.tag:
                diffs.append(ContainerDiff(
                    change_type="image_changed",
                    name=new_c.name,
                    host=new_c.host,
                    compose_stack=new_c.compose_stack,
                    old_tag=old["tag"],
                    new_tag=new_c.tag,
                ))
                image_changed_keys.add(key)

    for key, old in old_map.items():
        if key not in new_map:
            host = Host(old["host"])
            diffs.append(ContainerDiff(
                change_type="removed",
                name=old["name"],
                host=host,
                compose_stack=old.get("compose_stack"),
                old_tag=old["tag"],
                old_status=old["status"],
            ))

    # Status changes — only record if not already covered by an image change
    for key, new_c in new_map.items():
        if key in old_map and key not in image_changed_keys:
            old = old_map[key]
            if old["status"] != new_c.status:
                # Only record transitions that indicate intentional action
                interesting = {
                    ("running", "exited"),
                    ("exited", "running"),
                    ("running", "paused"),
                    ("paused", "running"),
                }
                if (old["status"], new_c.status) in interesting:
                    diffs.append(ContainerDiff(
                        change_type="status_changed",
                        name=new_c.name,
                        host=new_c.host,
                        compose_stack=new_c.compose_stack,
                        old_status=old["status"],
                        new_status=new_c.status,
                    ))

    return diffs


def diffs_to_events(
    diffs: list[ContainerDiff],
    timestamp: datetime,
) -> list[ChangelogEvent]:
    """Convert a list of ContainerDiffs into ChangelogEvents."""
    events: list[ChangelogEvent] = []
    for d in diffs:
        if d.change_type == "image_changed":
            event_type = "updated"
        elif d.change_type == "added":
            event_type = "created"
        elif d.change_type == "removed":
            event_type = "destroyed"
        elif d.change_type == "status_changed":
            event_type = "status_changed"
        else:
            event_type = d.change_type

        msg = None
        if d.change_type == "status_changed":
            msg = f"status changed `{d.old_status}` → `{d.new_status}`"

        events.append(ChangelogEvent(
            id=str(uuid.uuid4()),
            timestamp=timestamp,
            host=d.host,
            container_name=d.name,
            stack_name=d.compose_stack,
            event_type=event_type,
            old_image_tag=d.old_tag,
            new_image_tag=d.new_tag,
            message=msg,
            source="docker",
        ))
    return events
