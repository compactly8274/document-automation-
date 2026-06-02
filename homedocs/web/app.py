from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from homedocs.models import CATEGORY_ORDER, VALID_CATEGORIES
from homedocs.store.descriptions_loader import (
    load_descriptions,
    load_url_mappings,
    save_descriptions,
    save_url_mappings,
)

log = logging.getLogger("homedocs.web")

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")
HOMEDOCS_CONTAINER = os.environ.get("HOMEDOCS_CONTAINER", "homedocs")

INVENTORY_JSON = Path(OUTPUT_DIR) / "inventory.json"
INVENTORY_MD = Path(OUTPUT_DIR) / "inventory.md"
CHANGELOG_MD = Path(OUTPUT_DIR) / "changelog.md"

VALID_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = FastAPI(title="homedocs", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────


def _pop_flash(request: Request) -> Optional[dict]:
    sid = request.cookies.get("sid", "")
    if not sid:
        return None
    return _flash_store.pop(sid, None)


def _flash_set(sid: str, message: str, level: str = "success") -> None:
    """Stash a one-shot message for the next request, keyed by sid.

    FastAPI doesn't ship sessions, so we use a tiny module-level dict keyed by
    a session id from a cookie. This is fine for a single-operator homelab tool
    (documented in the README).
    """
    if not sid:
        return
    _flash_store[sid] = {"message": message, "level": level}


_flash_store: dict[str, dict] = {}


def _new_sid() -> str:
    import secrets
    return secrets.token_urlsafe(16)


def _ensure_sid_cookie(response: RedirectResponse, request: Request) -> str:
    """Make sure the response carries an sid cookie; return the sid in use.

    Returns the incoming sid if present (so callers can stash a flash under it),
    otherwise mints a new one and sets it on the response.
    """
    sid = request.cookies.get("sid", "")
    if not sid:
        sid = _new_sid()
        response.set_cookie("sid", sid, httponly=True, samesite="lax")
    return sid


def _read_inventory() -> Optional[dict]:
    if not INVENTORY_JSON.exists():
        return None
    try:
        with INVENTORY_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to read %s: %s", INVENTORY_JSON, e)
        return None


def _read_changelog_md() -> Optional[str]:
    if not CHANGELOG_MD.exists():
        return None
    try:
        return CHANGELOG_MD.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("Failed to read %s: %s", CHANGELOG_MD, e)
        return None


def _trigger_regen() -> tuple[bool, str]:
    """Best-effort: ask the homedocs container to regenerate.

    Mirrors the `log.sh` pattern from the host.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", HOMEDOCS_CONTAINER, "python", "-m", "homedocs", "regenerate"],
            check=False,
            timeout=60,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout or "unknown error").strip()
    except FileNotFoundError:
        return False, "docker CLI not available in this container"
    except subprocess.TimeoutExpired:
        return False, "regenerate timed out"
    except Exception as e:
        return False, str(e)


def _group_by_category(containers: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in containers:
        groups[c.get("category", "Misc")].append(c)
    # Sort each group by name
    for k in groups:
        groups[k].sort(key=lambda x: x.get("name", ""))
    # Return in canonical category order, with any unknown categories at the end
    ordered: dict[str, list[dict]] = {}
    for cat in CATEGORY_ORDER:
        if groups.get(cat.value):
            ordered[cat.value] = groups[cat.value]
    for k in sorted(groups.keys()):
        if k not in ordered:
            ordered[k] = groups[k]
    return ordered


def _status_badge(status: str) -> str:
    return {
        "running": "🟢 running",
        "exited": "🔴 exited",
        "paused": "🟡 paused",
        "restarting": "🔄 restarting",
    }.get(status, status or "?")


def _fmt_date_iso(s: Optional[str]) -> str:
    if not s:
        return "—"
    return s


# ── Routes ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    inv = _read_inventory()
    flash = _pop_flash(request)

    if inv is None:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "flash": flash,
                "summary": None,
                "last_regen": None,
            },
        )

    containers = inv.get("containers", [])
    generated_at = inv.get("generated_at", "")
    try:
        last_regen = datetime.fromisoformat(generated_at).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        last_regen = generated_at or "unknown"

    by_cat: dict[str, int] = {}
    for c in containers:
        by_cat[c.get("category", "Misc")] = by_cat.get(c.get("category", "Misc"), 0) + 1

    summary = {
        "total": len(containers),
        "running": sum(1 for c in containers if c.get("status") == "running"),
        "by_category": by_cat,
        "unraid_reachable": inv.get("unraid_reachable", False),
        "truenas_reachable": inv.get("truenas_reachable", False),
    }

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "flash": flash,
            "summary": summary,
            "last_regen": last_regen,
        },
    )


@app.get("/inventory", response_class=HTMLResponse)
async def inventory(request: Request):
    inv = _read_inventory()
    flash = _pop_flash(request)

    if inv is None:
        return templates.TemplateResponse(
            request,
            "inventory.html",
            {
                "flash": flash,
                "groups": {},
                "reachable_warnings": [],
                "generated_at": "never",
                "fmt_date": _fmt_date_iso,
                "status_badge": _status_badge,
                "empty": True,
            },
        )

    groups = _group_by_category(inv.get("containers", []))
    reachable_warnings: list[str] = []
    if not inv.get("unraid_reachable", True):
        reachable_warnings.append("Unraid is unreachable — data for this host may be missing.")
    if not inv.get("truenas_reachable", True):
        reachable_warnings.append("TrueNAS is unreachable — data for this host may be missing.")

    try:
        generated_at = (
            datetime.fromisoformat(inv.get("generated_at", ""))
            .strftime("%Y-%m-%d %H:%M UTC")
        )
    except (TypeError, ValueError):
        generated_at = inv.get("generated_at", "unknown")

    return templates.TemplateResponse(
        request,
        "inventory.html",
        {
            "flash": flash,
            "groups": groups,
            "reachable_warnings": reachable_warnings,
            "generated_at": generated_at,
            "fmt_date": _fmt_date_iso,
            "status_badge": _status_badge,
            "empty": False,
        },
    )


@app.get("/changelog", response_class=HTMLResponse)
async def changelog(request: Request):
    flash = _pop_flash(request)
    src = _read_changelog_md()
    if src is None:
        body = "<p><em>No changelog generated yet.</em></p>"
        generated_at = "never"
    else:
        body = md.markdown(src, extensions=["fenced_code", "tables"])
        # The renderer writes a top "Generated: ..." line. Strip it from the
        # body so we can show it as a separate header in the template.
        m = re.search(r"_Generated:\s*([^_]+)_", src)
        generated_at = (m.group(1).strip() if m else "unknown")

    return templates.TemplateResponse(
        request,
        "changelog.html",
        {
            "flash": flash,
            "body": body,
            "generated_at": generated_at,
            "empty": src is None,
        },
    )


@app.get("/config/descriptions", response_class=HTMLResponse)
async def edit_descriptions_get(request: Request):
    flash = _pop_flash(request)
    descriptions = load_descriptions(CONFIG_DIR)
    inv = _read_inventory()
    inventory_names = {c.get("name") for c in (inv or {}).get("containers", [])}

    # Union of descriptions.yaml keys and live container names, with the
    # inventory taking precedence on the ordering (so new containers appear
    # at the top). Stale description entries (no matching container) are kept
    # at the bottom so users can clean them up.
    live_names = [n for n in inventory_names if n]
    stale_names = [n for n in descriptions.keys() if n not in inventory_names]

    rows = []
    for name in live_names + stale_names:
        entry = descriptions.get(name, {})
        rows.append({
            "name": name,
            "description": entry.get("description") or "",
            "category": entry.get("category") or "Misc",
            "notes": entry.get("notes") or "",
            "date_first_deployed": entry.get("date_first_deployed") or "",
        })

    return templates.TemplateResponse(
        request,
        "edit_descriptions.html",
        {
            "flash": flash,
            "rows": rows,
            "categories": sorted(VALID_CATEGORIES),
            "error": None,
            "form_data": None,
        },
    )


@app.post("/config/descriptions")
async def edit_descriptions_post(request: Request):
    form = await request.form()
    names = form.getlist("name")

    new_rows = []
    errors: list[str] = []
    for raw_name in names:
        name = (raw_name or "").strip()
        if not name:
            continue
        desc = (form.get(f"description__{name}", "") or "").strip()
        cat = (form.get(f"category__{name}", "Misc") or "").strip()
        notes = (form.get(f"notes__{name}", "") or "").strip()
        date = (form.get(f"date__{name}", "") or "").strip()

        if cat not in VALID_CATEGORIES:
            errors.append(f"{name}: unknown category {cat!r}")
        if date and not VALID_DATE.match(date):
            errors.append(f"{name}: date must be YYYY-MM-DD or empty (got {date!r})")

        new_rows.append({
            "name": name,
            "description": desc,
            "category": cat,
            "notes": notes,
            "date_first_deployed": date,
        })

    if errors:
        # Re-render the form with the user's input preserved and an error banner
        return templates.TemplateResponse(
            request,
            "edit_descriptions.html",
            {
                "flash": None,
                "rows": new_rows,
                "categories": sorted(VALID_CATEGORIES),
                "error": "; ".join(errors),
                "form_data": None,
            },
            status_code=400,
        )

    # Build the dict for save_descriptions. Drop entries that are completely
    # empty so they don't pollute the file.
    out: dict[str, dict] = {}
    for r in new_rows:
        if not any([r["description"], r["notes"], r["date_first_deployed"]]) and r["category"] == "Misc":
            # Fully blank → skip
            continue
        out[r["name"]] = {
            "description": r["description"] or None,
            "category": r["category"],
            "notes": r["notes"] or None,
            "date_first_deployed": r["date_first_deployed"] or None,
        }

    try:
        save_descriptions(CONFIG_DIR, out)
    except Exception as e:
        log.exception("Failed to save descriptions.yaml")
        return templates.TemplateResponse(
            request,
            "edit_descriptions.html",
            {
                "flash": None,
                "rows": new_rows,
                "categories": sorted(VALID_CATEGORIES),
                "error": f"Failed to write descriptions.yaml: {e}",
                "form_data": None,
            },
            status_code=500,
        )

    ok, regen_err = _trigger_regen()
    resp = RedirectResponse(url="/config/descriptions", status_code=303)
    sid = _ensure_sid_cookie(resp, request)
    if ok:
        _flash_set(sid, "Descriptions saved. Docs regenerated.")
    else:
        _flash_set(
            sid,
            f"Descriptions saved, but regenerate failed: {regen_err}. "
            "The daemon will retry on its next interval.",
            level="warning",
        )
    return resp


@app.get("/config/urls", response_class=HTMLResponse)
async def edit_urls_get(request: Request):
    flash = _pop_flash(request)
    url_mappings = load_url_mappings(CONFIG_DIR)
    inv = _read_inventory()
    inventory_names = {c.get("name") for c in (inv or {}).get("containers", [])}

    live_names = [n for n in inventory_names if n]
    stale_names = [n for n in url_mappings.keys() if n not in inventory_names]

    rows = []
    for name in live_names + stale_names:
        val = url_mappings.get(name)
        # Display rule: None → "null" sentinel in the field
        rows.append({
            "name": name,
            "url": "" if val is None else val,
            "is_null": val is None,
        })

    return templates.TemplateResponse(
        request,
        "edit_urls.html",
        {
            "flash": flash,
            "rows": rows,
            "error": None,
            "form_data": None,
        },
    )


@app.post("/config/urls")
async def edit_urls_post(request: Request):
    form = await request.form()
    names = form.getlist("name")

    new_rows = []
    out: dict = {}
    errors: list[str] = []

    for raw_name in names:
        name = (raw_name or "").strip()
        if not name:
            continue
        raw = (form.get(f"url__{name}", "") or "").strip()
        if raw == "":
            # Treat empty as 'remove override' — skip from output entirely
            new_rows.append({"name": name, "url": "", "is_null": False})
            continue
        if raw.lower() == "null":
            out[name] = None
            new_rows.append({"name": name, "url": "", "is_null": True})
            continue
        if not (raw.startswith("http://") or raw.startswith("https://")):
            errors.append(f"{name}: URL must start with http:// or https:// (or be empty, or 'null')")
            new_rows.append({"name": name, "url": raw, "is_null": False})
            continue
        out[name] = raw
        new_rows.append({"name": name, "url": raw, "is_null": False})

    if errors:
        return templates.TemplateResponse(
            request,
            "edit_urls.html",
            {
                "flash": None,
                "rows": new_rows,
                "error": "; ".join(errors),
                "form_data": None,
            },
            status_code=400,
        )

    try:
        save_url_mappings(CONFIG_DIR, out)
    except Exception as e:
        log.exception("Failed to save url_mappings.yaml")
        return templates.TemplateResponse(
            request,
            "edit_urls.html",
            {
                "flash": None,
                "rows": new_rows,
                "error": f"Failed to write url_mappings.yaml: {e}",
                "form_data": None,
            },
            status_code=500,
        )

    ok, regen_err = _trigger_regen()
    resp = RedirectResponse(url="/config/urls", status_code=303)
    sid = _ensure_sid_cookie(resp, request)
    if ok:
        _flash_set(sid, "URL mappings saved. Docs regenerated.")
    else:
        _flash_set(
            sid,
            f"URL mappings saved, but regenerate failed: {regen_err}. "
            "The daemon will retry on its next interval.",
            level="warning",
        )
    return resp
