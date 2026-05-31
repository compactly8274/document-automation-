from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from homedocs.models import Host, HostConfig

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("1", "true", "yes")


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    hosts: list[HostConfig]
    domain: str
    output_dir: str
    config_dir: str

    github_token: str
    github_repo: str
    github_branch: str

    debounce_seconds: float
    regen_interval: int
    regenerate_on_start: bool

    log_level: str


def load_settings() -> Settings:
    hosts = []
    unraid_url = _env("UNRAID_SOCKET_URL", "tcp://192.168.1.104:2375")
    truenas_url = _env("TRUENAS_SOCKET_URL", "tcp://192.168.1.122:2375")

    if unraid_url:
        hosts.append(HostConfig(name="Unraid", label=Host.UNRAID, socket_url=unraid_url))
    if truenas_url:
        hosts.append(HostConfig(name="TrueNAS", label=Host.TRUENAS, socket_url=truenas_url))

    return Settings(
        hosts=hosts,
        domain=_env("DOMAIN", "pancakefarts.xyz"),
        output_dir=_env("OUTPUT_DIR", "/output"),
        config_dir=_env("CONFIG_DIR", "/config"),
        github_token=_env("GITHUB_TOKEN"),
        github_repo=_env("GITHUB_REPO"),
        github_branch=_env("GITHUB_BRANCH", "main"),
        debounce_seconds=_env_float("DEBOUNCE_SECONDS", 10.0),
        regen_interval=_env_int("REGEN_INTERVAL", 3600),
        regenerate_on_start=_env_bool("REGENERATE_ON_START", True),
        log_level=_env("LOG_LEVEL", "INFO"),
    )
