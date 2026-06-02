from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Host(str, Enum):
    UNRAID = "unraid"
    TRUENAS = "truenas"


class Category(str, Enum):
    MEDIA = "Media"
    ARR_STACK = "Arr Stack"
    BOOKS_COMICS = "Books & Comics"
    DOWNLOAD = "Download Clients"
    AI_SEARCH = "AI & Search"
    DOCUMENTS = "Documents & Files"
    INFRASTRUCTURE = "Infrastructure"
    MONITORING = "Monitoring"
    MISC = "Misc"


CATEGORY_ORDER = [
    Category.MEDIA,
    Category.ARR_STACK,
    Category.BOOKS_COMICS,
    Category.DOWNLOAD,
    Category.AI_SEARCH,
    Category.DOCUMENTS,
    Category.INFRASTRUCTURE,
    Category.MONITORING,
    Category.MISC,
]

VALID_CATEGORIES = {c.value for c in Category}


@dataclass
class PortMapping:
    host_port: int
    container_port: int
    protocol: str
    url: Optional[str] = None


@dataclass
class ContainerRecord:
    # Auto-detected
    name: str
    image: str
    tag: str
    host: Host
    status: str
    restart_policy: str
    compose_stack: Optional[str]
    networks: list[str]
    ports: list[PortMapping]
    image_source_url: Optional[str]
    image_last_updated: Optional[datetime]
    github_description: Optional[str]
    container_id: str

    # Config fields used for change detection
    bind_mounts: list[str] = field(default_factory=list)  # ["host_path:container_path"]
    env_hash: Optional[str] = None  # SHA256[:16] of all env vars (never stored in output)

    # User-provided (merged from descriptions.yaml)
    description: Optional[str] = None
    category: Category = Category.MISC
    notes: Optional[str] = None
    date_first_deployed: Optional[str] = None


@dataclass
class ChangelogEvent:
    id: str
    timestamp: datetime
    host: Optional[Host]
    container_name: Optional[str]
    stack_name: Optional[str]
    event_type: str  # created | destroyed | updated | config_changed | status_changed | manual
    old_image_tag: Optional[str]
    new_image_tag: Optional[str]
    message: Optional[str]
    source: str  # "docker" | "manual"
    details: list[str] = field(default_factory=list)  # sub-items for config changes


@dataclass
class HostConfig:
    name: str
    label: Host
    socket_url: str
    tls: bool = False
