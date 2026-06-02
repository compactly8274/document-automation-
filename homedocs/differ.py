from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from homedocs.models import ChangelogEvent, ContainerRecord, Host


@dataclass
class ContainerDiff:
    change_type: str  # "added" | "removed" | "image_changed" | "config_changed" | "status_changed"
    name: str
    host: Host
    compose_stack: Optional[str]
    old_tag: Optional[str] = None
    new_tag: Optional[str] = None
    old_status: Optional[str] = None
    new_status: Optional[str] = None
    config_changes: list[str] = field(default_factory=list)  # human-readable sub-changes


def _diff_config(old: dict, new_c: ContainerRecord) -> list[str]:
    """Return a list of human-readable descriptions of config changes."""
    changes: list[str] = []

    # Restart policy
    old_rp = old.get("restart_policy", "")
    if old_rp != new_c.restart_policy:
        changes.append(f"restart policy: `{old_rp}` → `{new_c.restart_policy}`")

    # Port bindings
    old_ports = set(old.get("port_bindings", []))
    new_ports = set(
        f"{p.host_port}:{p.container_port}/{p.protocol}" for p in new_c.ports
    )
    for p in sorted(old_ports - new_ports):
        changes.append(f"port removed: `{p}`")
    for p in sorted(new_ports - old_ports):
        changes.append(f"port added: `{p}`")

    # Bind mounts
    old_mounts = set(old.get("bind_mounts", []))
    new_mounts = set(new_c.bind_mounts)
    for m in sorted(old_mounts - new_mounts):
        changes.append(f"volume removed: `{m}`")
    for m in sorted(new_mounts - old_mounts):
        changes.append(f"volume added: `{m}`")

    # Networks
    old_nets = set(old.get("networks", []))
    new_nets = set(new_c.networks)
    for n in sorted(old_nets - new_nets):
        changes.append(f"network removed: `{n}`")
    for n in sorted(new_nets - old_nets):
        changes.append(f"network added: `{n}`")

    # Environment variables (hash-only, never expose values)
    old_hash = old.get("env_hash")
    new_hash = new_c.env_hash
    if old_hash and new_hash and old_hash != new_hash:
        changes.append("environment variables changed")

    return changes


def diff_snapshots(
    old_snap: list[dict],
    new_containers: list[ContainerRecord],
) -> list[ContainerDiff]:
    """
    Compare a saved snapshot against freshly collected containers.

    Precedence rules:
    - image_changed subsumes status_changed (status flip is a side effect of the update)
    - config_changes are attached to image_changed when both occur simultaneously
    - A container that changed only config (same image) gets change_type="config_changed"
    """
    old_map: dict[tuple, dict] = {(d["host"], d["name"]): d for d in old_snap}
    new_map: dict[tuple, ContainerRecord] = {(c.host.value, c.name): c for c in new_containers}

    diffs: list[ContainerDiff] = []
    handled_keys: set[tuple] = set()

    # Containers present in both snapshots — check for changes
    for key in old_map:
        if key not in new_map:
            continue
        old = old_map[key]
        new_c = new_map[key]
        handled_keys.add(key)

        tag_changed = old["tag"] != new_c.tag
        config_changes = _diff_config(old, new_c)

        if tag_changed:
            diffs.append(ContainerDiff(
                change_type="image_changed",
                name=new_c.name,
                host=new_c.host,
                compose_stack=new_c.compose_stack,
                old_tag=old["tag"],
                new_tag=new_c.tag,
                config_changes=config_changes,
            ))
        elif config_changes:
            diffs.append(ContainerDiff(
                change_type="config_changed",
                name=new_c.name,
                host=new_c.host,
                compose_stack=new_c.compose_stack,
                config_changes=config_changes,
            ))
        else:
            # Check status change only when nothing else changed
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

    # Added containers (not in old snapshot)
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

    # Removed containers (not in new state)
    for key, old in old_map.items():
        if key not in new_map:
            diffs.append(ContainerDiff(
                change_type="removed",
                name=old["name"],
                host=Host(old["host"]),
                compose_stack=old.get("compose_stack"),
                old_tag=old["tag"],
                old_status=old["status"],
            ))

    return diffs


def diffs_to_events(
    diffs: list[ContainerDiff],
    timestamp: datetime,
) -> list[ChangelogEvent]:
    events: list[ChangelogEvent] = []
    for d in diffs:
        if d.change_type == "image_changed":
            event_type = "updated"
        elif d.change_type == "added":
            event_type = "created"
        elif d.change_type == "removed":
            event_type = "destroyed"
        else:
            event_type = d.change_type  # "config_changed" | "status_changed"

        msg = None
        if d.change_type == "status_changed":
            msg = f"status `{d.old_status}` → `{d.new_status}`"

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
            details=d.config_changes,
        ))
    return events
