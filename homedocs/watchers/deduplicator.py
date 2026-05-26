from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from homedocs.models import ChangelogEvent, Host

log = logging.getLogger(__name__)


def _collapse(host: Host, container_name: str, events: list[dict]) -> ChangelogEvent:
    """Collapse a list of raw Docker events into a single ChangelogEvent."""
    actions = {e["action"] for e in events}
    timestamps = [e.get("time", 0) for e in events]
    ts = datetime.fromtimestamp(max(timestamps), tz=timezone.utc) if timestamps else datetime.now(timezone.utc)

    actor = events[-1].get("actor", {}) if events else {}
    attrs = actor.get("Attributes", {}) if isinstance(actor, dict) else {}
    stack_name = attrs.get("com.docker.compose.project")

    # Detect image change: look for 'image' attribute in actor
    image_attr = attrs.get("image", "")
    old_tag = new_tag = None

    if "pull" in actions:
        event_type = "pulled"
        if ":" in image_attr:
            new_tag = image_attr.split(":")[-1]
    elif "destroy" in actions and "create" not in actions and "start" not in actions:
        event_type = "destroyed"
    elif "create" in actions and "start" in actions and "die" not in actions:
        event_type = "created"
    elif "die" in actions or "stop" in actions:
        if "start" in actions:
            event_type = "updated"  # restart
        else:
            event_type = "destroyed"
    elif "start" in actions:
        event_type = "created"
    else:
        event_type = "updated"

    return ChangelogEvent(
        id=str(uuid.uuid4()),
        timestamp=ts,
        host=host,
        container_name=container_name,
        stack_name=stack_name or None,
        event_type=event_type,
        old_image_tag=old_tag,
        new_image_tag=new_tag,
        message=None,
        source="docker",
    )


class Deduplicator:
    def __init__(
        self,
        window_seconds: float,
        on_emit: Callable[[ChangelogEvent], None],
    ):
        self._window = window_seconds
        self._on_emit = on_emit
        self._buckets: dict[str, list[dict]] = defaultdict(list)
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def ingest(self, host: Host, container_name: str, raw_event: dict):
        key = f"{host.value}:{container_name}"
        with self._lock:
            self._buckets[key].append(raw_event)
            existing = self._timers.pop(key, None)
            if existing:
                existing.cancel()
            t = threading.Timer(self._window, self._flush, args=[key, host, container_name])
            self._timers[key] = t
            t.start()

    def _flush(self, key: str, host: Host, container_name: str):
        with self._lock:
            events = self._buckets.pop(key, [])
            self._timers.pop(key, None)
        if not events:
            return
        try:
            ev = _collapse(host, container_name, events)
            self._on_emit(ev)
        except Exception as e:
            log.error("Deduplicator flush error for %s: %s", key, e)

    def flush_all(self):
        """Cancel all pending timers and flush immediately."""
        with self._lock:
            keys = list(self._timers.keys())
            for key in keys:
                t = self._timers.pop(key, None)
                if t:
                    t.cancel()
            buckets = dict(self._buckets)
            self._buckets.clear()

        for key, events in buckets.items():
            if not events:
                continue
            parts = key.split(":", 1)
            try:
                host = Host(parts[0])
                container_name = parts[1] if len(parts) > 1 else key
                ev = _collapse(host, container_name, events)
                self._on_emit(ev)
            except Exception as e:
                log.error("Deduplicator flush_all error for %s: %s", key, e)
