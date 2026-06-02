from __future__ import annotations

import logging
import os
from typing import Optional

import yaml

from homedocs.models import Category, ContainerRecord, VALID_CATEGORIES

log = logging.getLogger(__name__)

# Heuristic keywords for auto-categorizing containers not yet in descriptions.yaml.
_CATEGORY_HEURISTICS: list[tuple[set[str], Category]] = [
    ({"plex", "jellyfin", "emby", "tautulli", "overseerr"}, Category.MEDIA),
    ({"sonarr", "radarr", "lidarr", "readarr", "prowlarr", "bazarr", "whisparr"}, Category.ARR_STACK),
    ({"kavita", "komga", "calibre"}, Category.BOOKS_COMICS),
    ({"sabnzbd", "qbittorrent", "transmission", "deluge", "nzbget", "jdownloader"}, Category.DOWNLOAD),
    ({"ollama", "open-webui", "openwebui", "llm"}, Category.AI_SEARCH),
    ({"paperless", "nextcloud", "syncthing"}, Category.DOCUMENTS),
    ({"traefik", "nginx", "authentik", "vaultwarden", "portainer", "wireguard", "tailscale", "cloudflared"}, Category.INFRASTRUCTURE),
    ({"grafana", "prometheus", "uptime-kuma", "uptimekuma", "loki"}, Category.MONITORING),
]


def auto_category(name: str, image: str) -> Category:
    """Guess a container's category from its name or image."""
    name_lower = name.lower()
    image_lower = image.lower()
    for keywords, cat in _CATEGORY_HEURISTICS:
        if any(k in name_lower for k in keywords) or any(k in image_lower for k in keywords):
            return cat
    return Category.MISC


def load_descriptions(config_dir: str) -> dict[str, dict]:
    path = os.path.join(config_dir, "descriptions.yaml")
    if not os.path.exists(path):
        log.warning("descriptions.yaml not found at %s — all containers will be uncategorized", path)
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    validated: dict[str, dict] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            log.warning("descriptions.yaml: entry %r is not a dict, skipping", name)
            continue
        cat = entry.get("category", "Misc")
        if cat not in VALID_CATEGORIES:
            log.warning("descriptions.yaml: unknown category %r for %r, defaulting to Misc", cat, name)
            cat = "Misc"
        validated[name] = {
            "description": entry.get("description"),
            "category": cat,
            "notes": entry.get("notes"),
            "date_first_deployed": entry.get("date_first_deployed"),
        }

    return validated


def load_url_mappings(config_dir: str) -> dict[str, Optional[str]]:
    path = os.path.join(config_dir, "url_mappings.yaml")
    if not os.path.exists(path):
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    result: dict[str, Optional[str]] = {}
    for name, entry in data.items():
        if isinstance(entry, dict):
            result[name] = entry.get("url")
        elif entry is None:
            result[name] = None
    return result


def merge_descriptions(containers: list[ContainerRecord], config_dir: str) -> tuple[list[ContainerRecord], set[str]]:
    """Merge user-supplied metadata into collected containers.

    Returns the updated container list plus a set of container names that have
    no descriptions.yaml entry (useful for prompting the user to add notes).
    """
    descriptions = load_descriptions(config_dir)
    warned: set[str] = set()
    missing: set[str] = set()

    for c in containers:
        entry = descriptions.get(c.name)
        if entry:
            c.description = entry.get("description")
            c.category = Category(entry["category"])
            c.notes = entry.get("notes")
            c.date_first_deployed = entry.get("date_first_deployed")
        else:
            missing.add(c.name)
            # Auto-categorize so new containers don't all land in Misc
            c.category = auto_category(c.name, c.image)
            if c.name not in warned:
                log.debug(
                    "No descriptions.yaml entry for container %r — auto-categorized as %s",
                    c.name, c.category.value,
                )
                warned.add(c.name)

    # Warn about descriptions.yaml entries that matched no running container
    running_names = {c.name for c in containers}
    for name in descriptions:
        if name not in running_names:
            log.warning("descriptions.yaml has entry for %r but no matching container was found", name)

    return containers, missing


def _atomic_write_yaml(path: str, data) -> None:
    """Write YAML to *path* via a sibling temp file + os.replace.

    Preserves dict insertion order and supports None values. Does not preserve
    comments — the web UI round-trips data only.
    """
    import tempfile

    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False,
            )
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_descriptions(config_dir: str, data: dict[str, dict]) -> None:
    """Write descriptions.yaml atomically. Caller is responsible for validation."""
    path = os.path.join(config_dir, "descriptions.yaml")
    # Drop keys whose entry is fully empty so the file stays tidy
    cleaned: dict[str, dict] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        cleaned[name] = {
            "description": entry.get("description") or None,
            "category": entry.get("category") or "Misc",
            "notes": entry.get("notes") or None,
            "date_first_deployed": entry.get("date_first_deployed") or None,
        }
    _atomic_write_yaml(path, cleaned)


def save_url_mappings(config_dir: str, data: dict) -> None:
    """Write url_mappings.yaml atomically. None values mean 'internal only'."""
    path = os.path.join(config_dir, "url_mappings.yaml")
    _atomic_write_yaml(path, data)


def init_descriptions_file(config_dir: str, containers: list[ContainerRecord]) -> str:
    """Scaffold (or update) descriptions.yaml from the current container list.

    Existing entries are preserved. New containers are appended with empty
    notes so the user can fill them in.  Returns the written file path.
    """
    path = os.path.join(config_dir, "descriptions.yaml")
    existing_names: set[str] = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        existing_names = set(data.keys())

    new = [c for c in containers if c.name not in existing_names]
    if not new:
        log.info("All %d containers already present in %s", len(containers), path)
        return path

    lines: list[str] = []
    if os.path.exists(path):
        lines.append("")
        lines.append("# ── New containers discovered since last init ─────────────────────────────────")
        lines.append("")
    else:
        lines = [
            "# ──────────────────────────────────────────────────────────────────────────────",
            "# descriptions.yaml — User-maintained metadata for the Container Inventory.",
            "#",
            "# Keys are container names exactly as shown by `docker ps --format '{{.Names}}'`.",
            "# All fields are optional — omitted fields fall back to auto-detected values.",
            "# Containers not listed here appear in the 'Misc' category with no description.",
            "#",
            "# Valid categories:",
            "#   Media | Arr Stack | Books & Comics | Download Clients | AI & Search |",
            "#   Documents & Files | Infrastructure | Monitoring | Misc",
            "# ──────────────────────────────────────────────────────────────────────────────",
            "",
        ]

    for c in sorted(new, key=lambda x: x.name):
        entry = {
            "description": c.github_description or "",
            "category": auto_category(c.name, c.image).value,
            "notes": "",
            "date_first_deployed": "",
        }
        dumped = yaml.safe_dump(
            {c.name: entry},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        lines.extend(dumped.splitlines())
        lines.append("")

    mode = "a" if os.path.exists(path) else "w"
    with open(path, mode, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log.info("Added %d new container entries to %s", len(new), path)
    return path
