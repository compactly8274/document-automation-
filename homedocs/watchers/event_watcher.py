from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Optional

from docker import DockerClient
from docker.errors import DockerException

from homedocs.models import HostConfig

log = logging.getLogger(__name__)

WATCHED_EVENT_TYPES = {
    "container": {"start", "stop", "destroy", "create", "die", "kill"},
    "image": {"pull"},
}


class EventWatcher(threading.Thread):
    def __init__(
        self,
        host: HostConfig,
        client_factory,  # callable(HostConfig) -> Optional[DockerClient]
        event_queue: queue.Queue,
        stop_event: threading.Event,
        retry_interval: float = 60.0,
    ):
        super().__init__(name=f"watcher-{host.label.value}", daemon=True)
        self.host = host
        self.client_factory = client_factory
        self.event_queue = event_queue
        self.stop_event = stop_event
        self.retry_interval = retry_interval

    def run(self):
        while not self.stop_event.is_set():
            client = self.client_factory(self.host)
            if client is None:
                log.warning("Watcher for %s: connection failed, retrying in %ds", self.host.name, int(self.retry_interval))
                self.stop_event.wait(timeout=self.retry_interval)
                continue

            log.info("Watcher for %s: subscribed to event stream", self.host.name)
            try:
                for event in client.events(decode=True):
                    if self.stop_event.is_set():
                        break
                    etype = event.get("Type", "")
                    action = event.get("Action", "")
                    if etype in WATCHED_EVENT_TYPES and action in WATCHED_EVENT_TYPES[etype]:
                        self.event_queue.put({
                            "host": self.host.label,
                            "type": etype,
                            "action": action,
                            "actor": event.get("Actor", {}),
                            "time": event.get("time", 0),
                            "raw": event,
                        })
            except DockerException as e:
                if self.stop_event.is_set():
                    break
                log.warning("Watcher for %s lost connection: %s — reconnecting in %ds", self.host.name, e, int(self.retry_interval))
                self.stop_event.wait(timeout=self.retry_interval)
            except Exception as e:
                log.error("Watcher for %s unexpected error: %s", self.host.name, e)
                self.stop_event.wait(timeout=self.retry_interval)
