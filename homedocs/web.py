from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

import markdown as md_lib
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

# Populated from __main__.py before the server starts
_output_dir: str = "/output"
_hosts: list = []
_regen_callback: Optional[Callable] = None
_regen_lock = threading.Lock()

app = FastAPI(title="HomeDocs", docs_url=None, redoc_url=None)

_CSS = """
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #7d8590;
  --accent: #7c6af7;
  --green: #3fb950;
  --red: #f85149;
  --yellow: #d29922;
  --code-bg: #1f2937;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 15px;
  line-height: 1.7;
}
nav {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 2rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  height: 52px;
  position: sticky;
  top: 0;
  z-index: 10;
}
.brand {
  font-weight: 700;
  font-size: 1rem;
  color: var(--accent);
  text-decoration: none;
  margin-right: 1.5rem;
  letter-spacing: -0.02em;
}
nav a {
  color: var(--muted);
  text-decoration: none;
  font-size: 0.875rem;
  padding: 5px 10px;
  border-radius: 6px;
  transition: all 0.1s;
}
nav a:hover { color: var(--text); background: var(--border); }
nav a.active { color: var(--text); background: var(--border); }
.spacer { flex: 1; }
.regen-btn {
  background: var(--accent);
  color: white;
  border: none;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  transition: opacity 0.1s;
}
.regen-btn:hover { opacity: 0.85; }
main { max-width: 1200px; margin: 0 auto; padding: 2rem 2rem 4rem; }
/* markdown content */
.md h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.md h2 {
  font-size: 1.1rem;
  margin: 2.5rem 0 0.75rem;
  padding-bottom: 0.4rem;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}
.md h3 { font-size: 0.95rem; margin: 1.5rem 0 0.5rem; color: var(--muted); }
.md > p:first-of-type { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.5rem; }
.md p { margin: 0.4rem 0; }
.md table { width: 100%; border-collapse: collapse; margin: 0.5rem 0 1.75rem; font-size: 0.84rem; }
.md th {
  background: var(--surface);
  color: var(--muted);
  text-align: left;
  padding: 7px 12px;
  border-bottom: 2px solid var(--border);
  font-weight: 600;
  white-space: nowrap;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.md td { padding: 7px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
.md tr:hover td { background: var(--surface); }
.md code {
  background: var(--code-bg);
  border: 1px solid var(--border);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.82em;
  font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  color: #a5b4fc;
}
.md blockquote {
  border-left: 3px solid var(--yellow);
  padding: 0.6rem 1rem;
  background: rgba(210, 153, 34, 0.08);
  border-radius: 0 6px 6px 0;
  margin: 1rem 0;
  color: var(--text);
}
.md ul, .md ol { padding-left: 1.5rem; margin: 0.3rem 0; }
.md li { margin: 0.15rem 0; font-size: 0.9rem; }
/* status page */
.status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.25rem 1.5rem;
}
.card-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 0.5rem; }
.card-value { font-size: 1.25rem; font-weight: 600; }
.host-row { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.75rem; }
.dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.dot-green { background: var(--green); box-shadow: 0 0 8px var(--green); }
.dot-red { background: var(--red); box-shadow: 0 0 8px var(--red); }
.host-name { font-weight: 600; font-size: 0.95rem; }
.host-url { font-size: 0.78rem; color: var(--muted); font-family: monospace; margin-left: auto; }
.section-title { font-size: 1rem; font-weight: 600; margin-bottom: 1rem; margin-top: 2rem; }
.toast {
  display: none;
  position: fixed;
  bottom: 1.5rem;
  right: 1.5rem;
  background: var(--green);
  color: #000;
  padding: 10px 18px;
  border-radius: 8px;
  font-weight: 600;
  font-size: 0.875rem;
  z-index: 100;
}
"""

_JS = """
async function triggerRegen() {
  const btn = document.getElementById('regen-btn');
  btn.textContent = 'Regenerating…';
  btn.style.opacity = '0.6';
  btn.style.pointerEvents = 'none';
  try {
    const r = await fetch('/api/regenerate', {method: 'POST'});
    const data = await r.json();
    btn.textContent = 'Regenerate';
    btn.style.opacity = '';
    btn.style.pointerEvents = '';
    const toast = document.getElementById('toast');
    toast.textContent = data.message || 'Done';
    toast.style.display = 'block';
    setTimeout(() => { toast.style.display = 'none'; }, 3000);
  } catch(e) {
    btn.textContent = 'Error';
    setTimeout(() => { btn.textContent = 'Regenerate'; btn.style.opacity = ''; btn.style.pointerEvents = ''; }, 2000);
  }
}
"""


def _shell(title: str, content: str, active: str) -> str:
    nav_links = [
        ("Inventory", "/", "inventory"),
        ("Changelog", "/changelog", "changelog"),
        ("Status", "/status", "status"),
    ]
    links_html = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for label, href, key in nav_links
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · HomeDocs</title>
<style>{_CSS}</style>
</head>
<body>
<nav>
  <a class="brand" href="/">🏠 HomeDocs</a>
  {links_html}
  <span class="spacer"></span>
  <button class="regen-btn" id="regen-btn" onclick="triggerRegen()">Regenerate</button>
</nav>
<main>{content}</main>
<div id="toast" class="toast">Done</div>
<script>{_JS}</script>
</body>
</html>"""


def _read_md(filename: str) -> str:
    path = os.path.join(_output_dir, filename)
    if not os.path.exists(path):
        return f"_`{filename}` not generated yet — trigger a regeneration._"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _render_md(source: str) -> str:
    return md_lib.markdown(
        source,
        extensions=["tables", "fenced_code"],
        output_format="html",
    )


def _last_regen() -> Optional[str]:
    path = os.path.join(_output_dir, "inventory.md")
    if not os.path.exists(path):
        return None
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return mtime.strftime("%Y-%m-%d %H:%M UTC")


@app.get("/", response_class=HTMLResponse)
def page_inventory():
    html = f'<div class="md">{_render_md(_read_md("inventory.md"))}</div>'
    return _shell("Inventory", html, "inventory")


@app.get("/changelog", response_class=HTMLResponse)
def page_changelog():
    html = f'<div class="md">{_render_md(_read_md("changelog.md"))}</div>'
    return _shell("Changelog", html, "changelog")


@app.get("/status", response_class=HTMLResponse)
def page_status():
    from homedocs.collectors.docker_client import make_client

    last = _last_regen() or "never"

    # Check host reachability live
    host_cards = ""
    for h in _hosts:
        ok = make_client(h) is not None
        dot_class = "dot-green" if ok else "dot-red"
        label = "Connected" if ok else "Unreachable"
        host_cards += f"""
        <div class="host-row">
          <span class="dot {dot_class}"></span>
          <span class="host-name">{h.name}</span>
          <span class="host-url">{h.socket_url}</span>
          <span style="font-size:0.8rem;color:{'var(--green)' if ok else 'var(--red)'};">{label}</span>
        </div>"""

    # Container + event counts from inventory.json
    container_count = "—"
    inv_path = os.path.join(_output_dir, "inventory.json")
    if os.path.exists(inv_path):
        import json
        try:
            with open(inv_path) as f:
                data = json.load(f)
            container_count = str(len(data.get("containers", [])))
        except Exception:
            pass

    changelog_count = "—"
    cl_path = os.path.join(_output_dir, ".changelog_events.jsonl")
    if os.path.exists(cl_path):
        try:
            with open(cl_path) as f:
                changelog_count = str(sum(1 for line in f if line.strip()))
        except Exception:
            pass

    content = f"""
    <h1 style="margin-bottom:1.5rem;">Status</h1>
    <div class="status-grid">
      <div class="card">
        <div class="card-title">Last Regenerated</div>
        <div class="card-value" style="font-size:1rem;">{last}</div>
      </div>
      <div class="card">
        <div class="card-title">Containers</div>
        <div class="card-value">{container_count}</div>
      </div>
      <div class="card">
        <div class="card-title">Changelog Events</div>
        <div class="card-value">{changelog_count}</div>
      </div>
    </div>
    <p class="section-title">Hosts</p>
    <div class="card" style="max-width:600px;">{host_cards}</div>
    """
    return _shell("Status", content, "status")


@app.get("/inventory.json")
def api_inventory_json():
    import json
    path = os.path.join(_output_dir, "inventory.json")
    if not os.path.exists(path):
        return JSONResponse({"error": "not generated yet"}, status_code=404)
    with open(path) as f:
        return JSONResponse(json.load(f))


@app.post("/api/regenerate")
def api_regenerate():
    if _regen_callback is None:
        return JSONResponse({"message": "Regeneration not available"}, status_code=503)
    if not _regen_lock.acquire(blocking=False):
        return JSONResponse({"message": "Regeneration already in progress"}, status_code=409)

    def _run():
        try:
            _regen_callback()
        finally:
            _regen_lock.release()

    threading.Thread(target=_run, daemon=True, name="web-regen").start()
    return JSONResponse({"message": "Regeneration started"})


def start_server(host: str = "0.0.0.0", port: int = 8080):
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)
