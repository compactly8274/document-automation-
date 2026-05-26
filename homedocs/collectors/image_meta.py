from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

import requests

log = logging.getLogger(__name__)

_GITHUB_TOKEN: Optional[str] = None
_request_counts: dict[str, int] = {}
_LIMITS = {
    "hub.docker.com": 90,
    "api.github.com": 55,
}


def configure(github_token: Optional[str] = None):
    global _GITHUB_TOKEN
    _GITHUB_TOKEN = github_token or None


def _headers(domain: str) -> dict:
    h: dict = {"User-Agent": "homedocs/1.0"}
    if domain == "api.github.com" and _GITHUB_TOKEN:
        h["Authorization"] = f"token {_GITHUB_TOKEN}"
        _LIMITS["api.github.com"] = 4900
    return h


def _budget_ok(domain: str) -> bool:
    count = _request_counts.get(domain, 0)
    limit = _LIMITS.get(domain, 50)
    if count >= limit:
        log.warning("API budget exhausted for %s (%d/%d), skipping lookup", domain, count, limit)
        return False
    return True


def _get(url: str, domain: str) -> Optional[dict]:
    if not _budget_ok(domain):
        return None
    try:
        resp = requests.get(url, headers=_headers(domain), timeout=5)
        _request_counts[domain] = _request_counts.get(domain, 0) + 1
        if resp.status_code == 200:
            return resp.json()
        log.debug("GET %s → %d", url, resp.status_code)
    except requests.RequestException as e:
        log.debug("GET %s failed: %s", url, e)
    return None


def _parse_image(image: str) -> tuple[str, str, str]:
    """Return (registry, namespace, repo) for a given image name."""
    image = image.split(":")[0]  # strip tag

    if image.startswith("ghcr.io/"):
        parts = image[len("ghcr.io/"):].split("/", 1)
        ns = parts[0] if len(parts) > 1 else "library"
        repo = parts[1] if len(parts) > 1 else parts[0]
        return "ghcr.io", ns, repo

    if image.startswith("lscr.io/linuxserver/"):
        repo = image[len("lscr.io/linuxserver/"):]
        return "hub.docker.com", "linuxserver", repo

    if "/" not in image:
        return "hub.docker.com", "library", image

    parts = image.split("/", 1)
    # Detect custom registry (contains a dot or colon)
    if "." in parts[0] or ":" in parts[0]:
        return parts[0], "", parts[1]

    return "hub.docker.com", parts[0], parts[1]


@lru_cache(maxsize=256)
def _dockerhub_meta(namespace: str, repo: str) -> Optional[dict]:
    ns = namespace or "library"
    url = f"https://hub.docker.com/v2/repositories/{ns}/{repo}/"
    return _get(url, "hub.docker.com")


@lru_cache(maxsize=256)
def _github_repo_meta(owner: str, repo: str) -> Optional[dict]:
    url = f"https://api.github.com/repos/{owner}/{repo}"
    return _get(url, "api.github.com")


def get_image_meta(image: str, source_label: Optional[str] = None) -> tuple[Optional[datetime], Optional[str]]:
    """Return (last_updated, description) for a given image."""
    registry, namespace, repo = _parse_image(image)

    # Try GitHub API if we have a source label pointing to GitHub
    if source_label and "github.com" in source_label:
        m = re.search(r"github\.com/([^/]+)/([^/\s]+)", source_label)
        if m:
            owner, gh_repo = m.group(1), m.group(2).rstrip(".git")
            data = _github_repo_meta(owner, gh_repo)
            if data:
                description = data.get("description")
                pushed = data.get("pushed_at")
                last_updated = None
                if pushed:
                    try:
                        last_updated = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    except ValueError:
                        pass
                return last_updated, description

    if registry == "hub.docker.com":
        data = _dockerhub_meta(namespace, repo)
        if data:
            description = data.get("description", "")[:200] if data.get("description") else None
            updated_str = data.get("last_updated")
            last_updated = None
            if updated_str:
                try:
                    last_updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
            return last_updated, description

    return None, None
