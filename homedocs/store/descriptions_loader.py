from __future__ import annotations

import logging
import os
from typing import Optional

import yaml

from homedocs.models import Category, ContainerRecord, VALID_CATEGORIES

log = logging.getLogger(__name__)


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


def merge_descriptions(containers: list[ContainerRecord], config_dir: str) -> list[ContainerRecord]:
    descriptions = load_descriptions(config_dir)
    warned: set[str] = set()

    for c in containers:
        entry = descriptions.get(c.name)
        if entry:
            c.description = entry.get("description")
            c.category = Category(entry["category"])
            c.notes = entry.get("notes")
            c.date_first_deployed = entry.get("date_first_deployed")
        else:
            if c.name not in warned:
                log.debug("No descriptions.yaml entry for container %r — categorized as Misc", c.name)
                warned.add(c.name)

    # Warn about descriptions.yaml entries that matched no running container
    running_names = {c.name for c in containers}
    for name in descriptions:
        if name not in running_names:
            log.warning("descriptions.yaml has entry for %r but no matching container was found", name)

    return containers
