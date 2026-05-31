from __future__ import annotations

import logging

import docker
from docker import DockerClient
from docker.errors import DockerException

from homedocs.models import HostConfig

log = logging.getLogger(__name__)


def make_client(host: HostConfig) -> DockerClient | None:
    try:
        client = docker.DockerClient(base_url=host.socket_url, timeout=10)
        client.ping()
        log.info("Connected to %s (%s)", host.name, host.socket_url)
        return client
    except DockerException as e:
        log.warning("Cannot reach %s (%s): %s", host.name, host.socket_url, e)
        return None
