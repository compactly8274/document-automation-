from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

from homedocs.models import Category, ContainerRecord, CATEGORY_ORDER, Host, PortMapping


def _fmt_ports(ports: list[PortMapping]) -> str:
    if not ports:
        return "—"
    parts = []
    seen_urls: set[str] = set()
    for p in ports:
        if p.url and p.url not in seen_urls:
            parts.append(f"[{p.host_port}]({p.url})")
            seen_urls.add(p.url)
        else:
            parts.append(str(p.host_port))
    return " ".join(parts)


def _fmt_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d")


def _status_badge(status: str) -> str:
    badges = {
        "running": "🟢 running",
        "exited": "🔴 exited",
        "paused": "🟡 paused",
        "restarting": "🔄 restarting",
    }
    return badges.get(status, status)


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
    """Escape markdown metacharacters for safe use in pipe tables."""
    return text.translate(_MD_META)


def _table_row(c: ContainerRecord) -> str:
    name = f"`{_esc_md(c.name)}`"
    image_tag = f"`{_esc_md(c.image)}:{_esc_md(c.tag)}`"
    host = c.host.value.capitalize()
    ports = _fmt_ports(c.ports)
    status = _status_badge(c.status)
    stack = f"`{_esc_md(c.compose_stack)}`" if c.compose_stack else "—"
    last_updated = _fmt_date(c.image_last_updated)
    desc = _esc_md(c.description or c.github_description or "")
    return f"| {name} | {image_tag} | {host} | {ports} | {status} | {stack} | {last_updated} | {desc} |"


def render_inventory_md(
    containers: list[ContainerRecord],
    reachable: dict[Host, bool],
    generated_at: Optional[datetime] = None,
) -> str:
    now = generated_at or datetime.now(timezone.utc)
    lines: list[str] = [
        "# Container Inventory",
        "",
        f"_Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]

    # Reachability warnings
    for host, ok in reachable.items():
        if not ok:
            lines.append(f"> **Warning:** {host.value.capitalize()} is unreachable — data for this host may be missing.")
            lines.append("")

    # Group by category in canonical order
    by_category: dict[Category, list[ContainerRecord]] = {cat: [] for cat in CATEGORY_ORDER}
    for c in containers:
        by_category[c.category].append(c)

    for cat in CATEGORY_ORDER:
        group = by_category[cat]
        if not group:
            continue
        lines.append(f"## {cat.value}")
        lines.append("")
        lines.append("| Container | Image:Tag | Host | Ports / URLs | Status | Stack | Last Updated | Description |")
        lines.append("|-----------|-----------|------|--------------|--------|-------|--------------|-------------|")
        for c in sorted(group, key=lambda x: x.name):
            lines.append(_table_row(c))
        lines.append("")

    if not any(containers):
        lines.append("_No containers found._")
        lines.append("")

    # Global notes section for containers that have notes
    all_with_notes = [c for c in containers if c.notes]
    if all_with_notes:
        lines.append("---")
        lines.append("")
        lines.append("## Notes")
        lines.append("")
        for c in sorted(all_with_notes, key=lambda x: (CATEGORY_ORDER.index(x.category), x.name)):
            note = _esc_md(c.notes)
            lines.append(f"- **{_esc_md(c.name)}** ({c.category.value}): {note}")
        lines.append("")

    return "\n".join(lines)


def _record_to_dict(c: ContainerRecord) -> dict:
    return {
        "name": c.name,
        "image": c.image,
        "tag": c.tag,
        "host": c.host.value,
        "status": c.status,
        "restart_policy": c.restart_policy,
        "compose_stack": c.compose_stack,
        "networks": c.networks,
        "ports": [
            {
                "host_port": p.host_port,
                "container_port": p.container_port,
                "protocol": p.protocol,
                "url": p.url,
            }
            for p in c.ports
        ],
        "image_source_url": c.image_source_url,
        "image_last_updated": c.image_last_updated.isoformat() if c.image_last_updated else None,
        "github_description": c.github_description,
        "container_id": c.container_id,
        "description": c.description,
        "category": c.category.value,
        "notes": c.notes,
        "date_first_deployed": c.date_first_deployed,
    }


def render_inventory_json(
    containers: list[ContainerRecord],
    reachable: dict[Host, bool],
    generated_at: Optional[datetime] = None,
) -> str:
    now = generated_at or datetime.now(timezone.utc)
    data = {
        "generated_at": now.isoformat(),
        "unraid_reachable": reachable.get(Host.UNRAID, False),
        "truenas_reachable": reachable.get(Host.TRUENAS, False),
        "containers": [_record_to_dict(c) for c in containers],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_inventory(
    output_dir: str,
    containers: list[ContainerRecord],
    reachable: dict[Host, bool],
    generated_at: Optional[datetime] = None,
):
    now = generated_at or datetime.now(timezone.utc)
    md = render_inventory_md(containers, reachable, now)
    js = render_inventory_json(containers, reachable, now)

    with open(os.path.join(output_dir, "inventory.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(output_dir, "inventory.json"), "w", encoding="utf-8") as f:
        f.write(js)
