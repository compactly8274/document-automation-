from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from homedocs.models import ChangelogEvent, Host

log = logging.getLogger(__name__)


def _serialize(event: ChangelogEvent) -> str:
    d = {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "host": event.host.value if event.host else None,
        "container_name": event.container_name,
        "stack_name": event.stack_name,
        "event_type": event.event_type,
        "old_image_tag": event.old_image_tag,
        "new_image_tag": event.new_image_tag,
        "message": event.message,
        "source": event.source,
        "details": event.details or [],
    }
    return json.dumps(d)


def _deserialize(line: str) -> Optional[ChangelogEvent]:
    try:
        d = json.loads(line)
        host = Host(d["host"]) if d.get("host") else None
        ts = datetime.fromisoformat(d["timestamp"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ChangelogEvent(
            id=d["id"],
            timestamp=ts,
            host=host,
            container_name=d.get("container_name"),
            stack_name=d.get("stack_name"),
            event_type=d["event_type"],
            old_image_tag=d.get("old_image_tag"),
            new_image_tag=d.get("new_image_tag"),
            message=d.get("message"),
            source=d.get("source", "docker"),
            details=d.get("details", []),  # backward-compat: older events have no details
        )
    except Exception as e:
        log.warning("Skipping malformed changelog line: %s — %s", line[:80], e)
        return None


class ChangelogStore:
    def __init__(self, output_dir: str):
        self._path = os.path.join(output_dir, ".changelog_events.jsonl")
        self._lock = threading.Lock()
        self._seen_ids: set[str] = set()
        self._events: list[ChangelogEvent] = []
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ev = _deserialize(line)
                if ev and ev.id not in self._seen_ids:
                    self._seen_ids.add(ev.id)
                    self._events.append(ev)
        log.info("Loaded %d changelog events from %s", len(self._events), self._path)

    def append(self, event: ChangelogEvent):
        with self._lock:
            if event.id in self._seen_ids:
                return
            self._seen_ids.add(event.id)
            self._events.append(event)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(_serialize(event) + "\n")

    def load_all(self) -> list[ChangelogEvent]:
        with self._lock:
            return list(self._events)
