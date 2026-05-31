from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

from homedocs.collectors import image_meta
from homedocs.collectors.container_collector import (
    collect_containers,
    load_previous_tags,
    save_current_tags,
)
from homedocs.collectors.docker_client import make_client
from homedocs.config import load_settings
from homedocs.git_publisher import publish
from homedocs.models import ChangelogEvent, Host
from homedocs.renderers.changelog_renderer import write_changelog
from homedocs.renderers.inventory_renderer import write_inventory
from homedocs.store.changelog_store import ChangelogStore
from homedocs.store.descriptions_loader import init_descriptions_file, merge_descriptions
from homedocs.watchers.deduplicator import Deduplicator
from homedocs.watchers.event_watcher import EventWatcher

log = logging.getLogger("homedocs")

# Containers we've already warned about missing from descriptions.yaml (per session).
_warned_missing: set[str] = set()


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def do_regenerate(settings, store: ChangelogStore, push: bool = True) -> tuple[dict[Host, bool], set[str]]:
    """Collect containers, merge metadata, render docs.

    Returns (reachability map, set of container names missing from descriptions.yaml).
    """
    os.makedirs(settings.output_dir, exist_ok=True)
    reachable: dict[Host, bool] = {}
    all_containers = []
    prev_tags = load_previous_tags(settings.output_dir)

    for host_cfg in settings.hosts:
        client = make_client(host_cfg)
        reachable[host_cfg.label] = client is not None
        if client:
            containers = collect_containers(client, host_cfg, settings.domain, settings.config_dir)
            # Detect image tag changes vs previous run
            for c in containers:
                old_tag = prev_tags.get(c.name)
                if old_tag and old_tag != c.tag:
                    ev = ChangelogEvent(
                        id=str(uuid.uuid4()),
                        timestamp=datetime.now(timezone.utc),
                        host=c.host,
                        container_name=c.name,
                        stack_name=c.compose_stack,
                        event_type="updated",
                        old_image_tag=old_tag,
                        new_image_tag=c.tag,
                        message=None,
                        source="docker",
                    )
                    store.append(ev)
                    log.info("Detected image change: %s %s→%s", c.name, old_tag, c.tag)
            all_containers.extend(containers)

    all_containers, missing = merge_descriptions(all_containers, settings.config_dir)
    save_current_tags(settings.output_dir, all_containers)

    now = datetime.now(timezone.utc)
    write_inventory(settings.output_dir, all_containers, reachable, now)
    write_changelog(settings.output_dir, store.load_all(), now)

    # Touch healthcheck marker so Docker HEALTHCHECK stays green
    try:
        hc = os.path.join(settings.output_dir, ".healthcheck")
        with open(hc, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except OSError:
        pass

    log.info("Regenerated docs: %d containers across %d host(s)", len(all_containers), sum(reachable.values()))

    if push and settings.github_token and settings.github_repo:
        publish(settings.output_dir, settings.github_token, settings.github_repo, settings.github_branch)

    return reachable, missing


def cmd_regenerate(settings, args):
    store = ChangelogStore(settings.output_dir)
    push = not args.no_push
    do_regenerate(settings, store, push=push)


def cmd_log(settings, args):
    message = " ".join(args.message)
    if not message.strip():
        print("Error: message cannot be empty", file=sys.stderr)
        sys.exit(1)

    store = ChangelogStore(settings.output_dir)
    ev = ChangelogEvent(
        id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        host=None,
        container_name=None,
        stack_name=None,
        event_type="manual",
        old_image_tag=None,
        new_image_tag=None,
        message=message,
        source="manual",
    )
    store.append(ev)
    log.info("Manual log entry added: %s", message)

    # Regenerate changelog to reflect new entry
    do_regenerate(settings, store, push=True)


def cmd_status(settings, args):
    print(f"Configured hosts:")
    for h in settings.hosts:
        try:
            client = make_client(h)
            ok = "✓ reachable" if client else "✗ unreachable"
        except Exception as e:
            ok = f"✗ error ({e})"
        print(f"  {h.name:10s}  {h.socket_url:40s}  {ok}")
    print(f"\nOutput dir : {settings.output_dir}")
    print(f"Config dir : {settings.config_dir}")
    last_inv = os.path.join(settings.output_dir, "inventory.md")
    if os.path.exists(last_inv):
        mtime = datetime.fromtimestamp(os.path.getmtime(last_inv), tz=timezone.utc)
        print(f"Last regen : {mtime.strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        print("Last regen : never")


def cmd_daemon(settings, args):
    os.makedirs(settings.output_dir, exist_ok=True)
    store = ChangelogStore(settings.output_dir)
    image_meta.configure(github_token=settings.github_token or None)

    stop_event = threading.Event()
    event_queue: queue.Queue = queue.Queue()
    regen_requested = threading.Event()

    def on_docker_event(ev: ChangelogEvent):
        store.append(ev)
        regen_requested.set()

    dedup = Deduplicator(
        window_seconds=settings.debounce_seconds,
        on_emit=on_docker_event,
    )

    def event_loop():
        while not stop_event.is_set():
            try:
                raw = event_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            host = raw["host"]
            actor = raw.get("actor", {})
            attrs = actor.get("Attributes", {}) if isinstance(actor, dict) else {}
            container_name = attrs.get("name") or actor.get("ID", "unknown")[:12]
            dedup.ingest(host, container_name, raw)

    # Start event consumer thread
    consumer = threading.Thread(target=event_loop, name="event-consumer", daemon=True)
    consumer.start()

    # Start one watcher per host
    watchers = []
    for host_cfg in settings.hosts:
        w = EventWatcher(
            host=host_cfg,
            client_factory=make_client,
            event_queue=event_queue,
            stop_event=stop_event,
        )
        w.start()
        watchers.append(w)

    if getattr(args, "no_regen_on_start", False):
        log.info("Skipping initial regeneration (--no-regen-on-start)")
    elif settings.regenerate_on_start:
        log.info("Running initial regeneration...")
        _, missing = do_regenerate(settings, store, push=True)
        _warned_missing.update(missing)

    def shutdown(signum, frame):
        log.info("Shutdown signal received, flushing...")
        stop_event.set()
        dedup.flush_all()
        do_regenerate(settings, store, push=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("Daemon running. Regen interval: %ds. Debounce: %ss.",
             settings.regen_interval, settings.debounce_seconds)

    last_regen = time.monotonic()
    while not stop_event.is_set():
        now = time.monotonic()
        interval_due = (now - last_regen) >= settings.regen_interval
        event_due = regen_requested.is_set()

        if interval_due or event_due:
            regen_requested.clear()
            _, missing = do_regenerate(settings, store, push=True)
            last_regen = time.monotonic()

            # Warn once per daemon session about containers without descriptions.yaml entries
            new_missing = missing - _warned_missing
            if new_missing:
                log.warning(
                    "New containers without descriptions.yaml entries: %s. "
                    "Run 'python -m homedocs init-config' to scaffold them.",
                    ", ".join(sorted(new_missing)),
                )
                _warned_missing.update(new_missing)

        stop_event.wait(timeout=5.0)


def cmd_init_config(settings, args):
    """Scaffold descriptions.yaml from currently running containers."""
    all_containers = []
    for host_cfg in settings.hosts:
        client = make_client(host_cfg)
        if client:
            containers = collect_containers(client, host_cfg, settings.domain, settings.config_dir)
            all_containers.extend(containers)
    all_containers, _ = merge_descriptions(all_containers, settings.config_dir)
    path = init_descriptions_file(settings.config_dir, all_containers)
    print(f"Scaffolded descriptions.yaml at {path}")
    print("Edit the 'notes' fields, then restart the daemon to pick up changes.")


def main():
    parser = argparse.ArgumentParser(prog="homedocs", description="Homelab documentation generator")
    sub = parser.add_subparsers(dest="command")

    # daemon
    p_daemon = sub.add_parser("daemon", help="Run the event-watching daemon")
    p_daemon.add_argument("--no-regen-on-start", action="store_true", help="Skip initial regeneration")

    # regenerate
    p_regen = sub.add_parser("regenerate", help="One-shot rebuild of all docs")
    p_regen.add_argument("--no-push", action="store_true", help="Skip git push")
    p_regen.add_argument("--host", choices=["unraid", "truenas", "all"], default="all")

    # log
    p_log = sub.add_parser("log", help="Append a manual changelog entry")
    p_log.add_argument("message", nargs="+", help="Log message text")

    # status
    sub.add_parser("status", help="Print host connection status and last regen time")

    # init-config
    p_init = sub.add_parser("init-config", help="Scaffold descriptions.yaml from running containers")
    p_init.add_argument("--host", choices=["unraid", "truenas", "all"], default="all")

    args = parser.parse_args()

    settings = load_settings()
    setup_logging(settings.log_level)
    image_meta.configure(github_token=settings.github_token or None)

    if args.command == "daemon" or args.command is None:
        cmd_daemon(settings, args)
    elif args.command == "regenerate":
        os.makedirs(settings.output_dir, exist_ok=True)
        store = ChangelogStore(settings.output_dir)
        do_regenerate(settings, store, push=not args.no_push)
    elif args.command == "log":
        os.makedirs(settings.output_dir, exist_ok=True)
        cmd_log(settings, args)
    elif args.command == "status":
        cmd_status(settings, args)
    elif args.command == "init-config":
        cmd_init_config(settings, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
