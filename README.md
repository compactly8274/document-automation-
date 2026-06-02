# Homelab Documentation Generator

Automatically generates and maintains two living documents from your Docker environment:

- **`inventory.md`** — container catalog grouped by category, with image metadata, ports, URLs, and your own notes
- **`changelog.md`** — reverse-chronological event log of container creates, destroys, updates, and image pulls

Watches Docker socket proxies on both Unraid and TrueNAS continuously. Docs regenerate on events and on a configurable interval.

---

## Requirements

- Docker socket proxy ([Tecnativa](https://github.com/Tecnativa/docker-socket-proxy)) running on each host with `CONTAINERS=1 IMAGES=1 EVENTS=1`
- Docker + Docker Compose on the machine running this stack

---

## Quick Start

**1. Clone the repo and copy the env file:**

```bash
git clone https://github.com/compactly8274/document-automation-.git homedocs
cd homedocs
cp .env.example .env
```

**2. Edit `.env` if your socket proxy ports differ from the defaults:**

```bash
# defaults shown — edit only what differs
UNRAID_SOCKET_URL=tcp://192.168.1.104:2375
TRUENAS_SOCKET_URL=tcp://192.168.1.122:2375
DOMAIN=pancakefarts.xyz
```

**3. Edit `config/descriptions.yaml`** to fill in descriptions, categories, and deploy dates for your containers. Any container not listed defaults to the `Misc` category.

**4. Start the stack:**

```bash
docker compose up -d
```

Docs are written to `./output/` on first start and kept current as your stack changes.

---

## Web UI

A small FastAPI app is included for browsing the generated docs and editing
`descriptions.yaml` / `url_mappings.yaml` from a browser. It runs as a second
service in the same compose stack.

```bash
# Default URL: http://localhost:27531 (override with WEB_PORT in .env)
open http://localhost:27531
```

Pages:

- `/` — summary, host reachability, category counts
- `/inventory` — full container inventory, grouped by category
- `/changelog` — Markdown-rendered changelog
- `/config/descriptions` — edit `descriptions.yaml` (form per container)
- `/config/urls` — edit `url_mappings.yaml` (form per container)

Saving a form rewrites the YAML atomically and triggers a regenerate on the
`homedocs` container via `docker exec` — the same path `log.sh` uses.

**Caveats:**

- The web service is intended for a trusted network. There is no authentication.
- Form-based edits do not preserve comments in the existing YAML files.
- Concurrent edits to the same file are last-write-wins (no locking).
- The web container uses the same image as the daemon. Building the image
  locally (`docker compose build`) installs `docker-ce-cli` (~100 MB) so it can
  run `docker exec` against the daemon.

---

## Docker Image

Pre-built image published to GitHub Container Registry on every push to `main`:

```
ghcr.io/compactly8274/document-automation:latest
```

---

## Configuration

All settings are environment variables. Set them in `.env` or directly in `docker-compose.yml`.

| Variable | Default | Description |
|----------|---------|-------------|
| `UNRAID_SOCKET_URL` | `tcp://192.168.1.104:2375` | Docker socket proxy URL for Unraid |
| `TRUENAS_SOCKET_URL` | `tcp://192.168.1.122:2375` | Docker socket proxy URL for TrueNAS |
| `DOMAIN` | `pancakefarts.xyz` | Base domain for auto-generated service URLs |
| `OUTPUT_DIR` | `/output` | Where docs are written (inside the container) |
| `CONFIG_DIR` | `/config` | Where config files are read from (inside the container) |
| `REGENERATE_ON_START` | `true` | Rebuild docs immediately on container start |
| `DEBOUNCE_SECONDS` | `10` | Wait this long after a Docker event before regenerating |
| `REGEN_INTERVAL` | `3600` | Unconditional full regeneration interval (seconds) |
| `GITHUB_TOKEN` | _(unset)_ | Fine-grained PAT (Contents: Read+Write) for auto-pushing docs. See [Auto-push to GitHub](#auto-push-to-github). |
| `GITHUB_REPO` | _(unset)_ | GitHub repo to push docs to, e.g. `yourname/homelab-docs` |
| `GITHUB_BRANCH` | `main` | Branch to push to. Recommend `docs` on first push; see [Auto-push to GitHub](#auto-push-to-github). |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

---

## Config Files

Both files live in `./config/` and are mounted read-only into the container.

### `config/descriptions.yaml`

User-maintained metadata merged with auto-detected container data. Keys are container names exactly as shown by `docker ps`.

```yaml
sonarr:
  description: "Automatic TV series downloader and organizer"
  category: "Arr Stack"
  notes: "Connected to SABnzbd and qBittorrent."
  date_first_deployed: "2024-03-15"
```

**Valid categories:** `Media` · `Arr Stack` · `Books & Comics` · `Download Clients` · `AI & Search` · `Documents & Files` · `Infrastructure` · `Monitoring` · `Misc`

A starter file pre-populated with common homelab containers is included. Containers not listed default to `Misc` with no description.

### `config/url_mappings.yaml`

Override the auto-generated URL (`{container_name}.{domain}`) for specific containers, or suppress it for internal-only services.

```yaml
plex:
  url: "https://plex.pancakefarts.xyz"   # override auto-pattern

postgres:
  url: null   # internal only — no URL shown in inventory
```

---

## CLI

### Manual changelog entries

From the host, with the container running:

```bash
./log.sh "Switched Ollama to gemma3:12b for better reasoning"
```

The entry is appended immediately and flagged as `[manual]` in `changelog.md`.

### Other commands

```bash
# Force a full rebuild
docker exec homedocs python -m homedocs regenerate

# Check host connectivity and last regen time
docker exec homedocs python -m homedocs status

# Rebuild without pushing to GitHub
docker exec homedocs python -m homedocs regenerate --no-push
```

---

## Output

### `output/inventory.md`

One table per category, auto-sorted. Columns: container name, image:tag, host, ports/URLs, status, stack, image last updated, description.

### `output/inventory.json`

Machine-readable version of the same data. Useful for dashboards or other tooling.

### `output/changelog.md`

Reverse-chronological event log grouped by week. Docker events (creates, destroys, image updates) are recorded automatically. Manual entries via `log.sh` appear with a `[manual]` badge.

---

## Auto-push to GitHub

Set `GITHUB_TOKEN`, `GITHUB_REPO`, and optionally `GITHUB_BRANCH` to have docs committed and pushed after each regeneration. The bot only needs to read the current state of `output/` and write the updated `inventory.md`, `inventory.json`, and `changelog.md`, so a fine-grained PAT scoped to the single docs repo is the right tool.

### 1. Create a fine-grained PAT

GitHub → avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.

| Field | Value |
|-------|-------|
| Token name | e.g. `homedocs-push` |
| Expiration | 90 days (max 1 year — do **not** set "No expiration") |
| Resource owner | your account |
| Repository access | **Only select repositories** → pick the docs repo |
| Repository permissions → **Contents** | **Read and Write** |

Generate, then copy the token. It starts with `github_pat_...`.

> **Why fine-grained?** It only has access to the one repo you selected, expires automatically, and the blast radius if it leaks is small. Classic PATs (`ghp_...`) work too, but grant broader access.

### 2. Configure `.env`

```bash
# .env
GITHUB_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxx
GITHUB_REPO=yourname/homelab-docs
# GITHUB_BRANCH=docs   # see "Branch choice" below — don't push to main on first run
```

### 3. Branch choice — read this before the first push

On the first regeneration with `GITHUB_TOKEN` + `GITHUB_REPO` set, the daemon `git init`s `/output`, commits the current `inventory.md` / `inventory.json` / `changelog.md`, and **force-pushes them to the configured branch**. If you point it at `main` on a repo that already has content, the homedocs output will replace it.

Pick one of:

- **Best — push to a non-main branch** (e.g. `docs`). Set `GITHUB_BRANCH=docs` in `.env`, browse the rendered output on the `docs` branch in GitHub, and merge into `main` yourself when you're happy with it. The first push creates the branch.
- **Push to a brand-new empty repo** on `main`. Safe if the repo has no other content.
- **Push to a populated `main`.** Possible, but you'll be overwriting prior content. Back up first.

### 4. Restart and verify

```bash
docker compose up -d
docker logs homedocs --tail 30 | grep -iE 'github|push|publish'
# Expected: a single "Pushed N files to owner/repo@branch" line per regen
```

A `403` in the logs almost always means the token doesn't have Contents: Write on the target repo, or the repo name (`GITHUB_REPO`) doesn't match. A `404` means the repo or branch doesn't exist / the token can't see it.

> **Never commit the token.** `.env` is already in `.gitignore`. If you ever need to rotate, regenerate from GitHub, update `.env`, and `docker compose up -d` to pick up the new value.

The output directory is initialized as a git repo on first run. Only `inventory.md`, `inventory.json`, and `changelog.md` are committed — internal state files are excluded via `.gitignore`.

---

## Socket Proxy Setup

This stack connects to your hosts over TCP — it does **not** mount `/var/run/docker.sock` directly. Each host needs a [Tecnativa docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) with at minimum:

```yaml
environment:
  - CONTAINERS=1
  - IMAGES=1
  - EVENTS=1
```

The proxy should be accessible at the IP and port configured in `UNRAID_SOCKET_URL` / `TRUENAS_SOCKET_URL`.

---

## Project Layout

```
.
├── docker-compose.yml
├── Dockerfile
├── log.sh                    # manual changelog entry CLI
├── config/
│   ├── descriptions.yaml     # your container metadata
│   └── url_mappings.yaml     # port → URL overrides
├── output/                   # generated docs land here
│   ├── inventory.md
│   ├── inventory.json
│   └── changelog.md
└── homedocs/                 # Python package
    ├── collectors/           # Docker socket + image registry APIs
    ├── watchers/             # event stream threads + debounce
    ├── renderers/            # Markdown + JSON output generators
    └── store/                # changelog persistence + descriptions loader
```
