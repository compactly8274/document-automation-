from __future__ import annotations

import json
import logging
import os
from typing import Optional

from docker import DockerClient

from homedocs.collectors.image_meta import get_image_meta
from homedocs.models import Category, ContainerRecord, Host, HostConfig, PortMapping
from homedocs.store.descriptions_loader import load_url_mappings

log = logging.getLogger(__name__)


def _split_image_tag(image_ref: str) -> tuple[str, str]:
    """Split 'repo:tag' into ('repo', 'tag'). Handles digests too."""
    if "@" in image_ref:
        image, digest = image_ref.split("@", 1)
        return image, digest[:16] + "..."
    if ":" in image_ref.split("/")[-1]:
        last_colon = image_ref.rfind(":")
        return image_ref[:last_colon], image_ref[last_colon + 1:]
    return image_ref, "latest"


def _resolve_url(
    container_name: str,
    host_port: int,
    domain: str,
    url_mappings: dict[str, Optional[str]],
) -> Optional[str]:
    if container_name in url_mappings:
        return url_mappings[container_name]
    # Auto-pattern: {name}.domain
    return f"https://{container_name}.{domain}"


def _parse_ports(container, domain: str, name: str, url_mappings: dict) -> list[PortMapping]:
    ports: list[PortMapping] = []
    bindings = container.ports or {}
    for container_port_proto, host_bindings in bindings.items():
        if not host_bindings:
            continue
        port_str, proto = container_port_proto.split("/") if "/" in container_port_proto else (container_port_proto, "tcp")
        container_port = int(port_str)
        for binding in (host_bindings or []):
            host_port = int(binding.get("HostPort", 0))
            if not host_port:
                continue
            url = _resolve_url(name, host_port, domain, url_mappings)
            ports.append(PortMapping(
                host_port=host_port,
                container_port=container_port,
                protocol=proto,
                url=url,
            ))
    return ports


def collect_containers(
    client: DockerClient,
    host_config: HostConfig,
    domain: str,
    config_dir: str,
) -> list[ContainerRecord]:
    url_mappings = load_url_mappings(config_dir)
    records: list[ContainerRecord] = []

    try:
        containers = client.containers.list(all=True)
    except Exception as e:
        log.error("Failed to list containers on %s: %s", host_config.name, e)
        return []

    for c in containers:
        labels = c.labels or {}

        # Stack name: compose project label first, then Unraid template label
        compose_stack = (
            labels.get("com.docker.compose.project")
            or labels.get("net.unraid.docker.template")
        )

        # Image + tag
        image_ref = c.image.tags[0] if c.image.tags else (c.image.short_id or "unknown")
        image, tag = _split_image_tag(image_ref)

        # Networks
        net_settings = c.attrs.get("NetworkSettings", {})
        networks = list((net_settings.get("Networks") or {}).keys())

        # Ports
        ports = _parse_ports(c, domain, c.name, url_mappings)

        # Restart policy
        host_config_attrs = c.attrs.get("HostConfig", {})
        restart_policy = host_config_attrs.get("RestartPolicy", {}).get("Name", "no")

        # Image source label
        source_label = labels.get("org.opencontainers.image.source")

        # Image metadata (last updated, description from registry/github)
        last_updated, github_desc = get_image_meta(image, source_label)

        records.append(ContainerRecord(
            name=c.name,
            image=image,
            tag=tag,
            host=host_config.label,
            status=c.status,
            restart_policy=restart_policy,
            compose_stack=compose_stack,
            networks=networks,
            ports=ports,
            image_source_url=source_label,
            image_last_updated=last_updated,
            github_description=github_desc,
            container_id=c.short_id,
        ))

    log.info("Collected %d containers from %s", len(records), host_config.name)
    return records


def load_snapshot(output_dir: str) -> list[dict]:
    """Load the previous container state snapshot. Returns [] on first run."""
    path = os.path.join(output_dir, ".snapshot.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


def save_snapshot(output_dir: str, containers: list[ContainerRecord]):
    """Persist the minimal container state needed for diffing."""
    path = os.path.join(output_dir, ".snapshot.json")
    data = [
        {
            "name": c.name,
            "image": c.image,
            "tag": c.tag,
            "host": c.host.value,
            "status": c.status,
            "restart_policy": c.restart_policy,
            "compose_stack": c.compose_stack,
        }
        for c in containers
    ]
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
