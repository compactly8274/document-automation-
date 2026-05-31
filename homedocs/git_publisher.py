from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_GIT_FILES = ["inventory.md", "inventory.json", "changelog.md"]


def _run(args: list[str], cwd: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check, env=env)


def _git(args: list[str], cwd: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return _run(["git"] + args, cwd=cwd, check=check, env=env)


def _ensure_git_repo(output_dir: str):
    if not os.path.exists(os.path.join(output_dir, ".git")):
        _git(["init", "-b", "main"], cwd=output_dir)
        log.info("Initialized git repo in %s", output_dir)
        # Write .gitignore to exclude internal state files
        gitignore_path = os.path.join(output_dir, ".gitignore")
        with open(gitignore_path, "w") as f:
            f.write(".changelog_events.jsonl\n.last_known_tags.json\n")
        _git(["add", ".gitignore"], cwd=output_dir)
        _git(["commit", "--allow-empty", "-m", "chore: init docs repo"], cwd=output_dir)


def _make_netrc_env(token: str) -> tuple[dict[str, str], str]:
    """Return an env dict with a temporary HOME dir containing .netrc for GitHub auth.

    Git reads ~/.netrc, so we point HOME at a temp directory.  The caller must
    delete the temp directory after use.
    """
    netrc_content = f"machine github.com\nlogin {token}\npassword x-oauth-basic\n"
    tmpdir = tempfile.mkdtemp(prefix="homedocs_git_")
    netrc_path = os.path.join(tmpdir, ".netrc")
    with open(netrc_path, "w") as f:
        f.write(netrc_content)
    os.chmod(netrc_path, 0o600)
    env = os.environ.copy()
    env["HOME"] = tmpdir
    return env, tmpdir


def publish(
    output_dir: str,
    token: str,
    repo: str,
    branch: str,
):
    if not token or not repo:
        log.debug("Git publish skipped: GITHUB_TOKEN or GITHUB_REPO not set")
        return

    _ensure_git_repo(output_dir)

    _git(["config", "user.email", "homedocs@localhost"], cwd=output_dir)
    _git(["config", "user.name", "HomeDocs Bot"], cwd=output_dir)

    # Stage only the published doc files that exist
    files_to_add = [f for f in _GIT_FILES if os.path.exists(os.path.join(output_dir, f))]
    if not files_to_add:
        log.warning("No files to commit in %s", output_dir)
        return

    _git(["add"] + files_to_add, cwd=output_dir)

    # Check if there's anything to commit
    result = _git(["diff", "--cached", "--quiet"], cwd=output_dir, check=False)
    if result.returncode == 0:
        log.info("Git publish: no changes to commit")
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _git(["commit", "-m", f"docs: regenerate {now}"], cwd=output_dir)

    env, tmpdir = _make_netrc_env(token)
    remote_url = f"https://github.com/{repo}.git"
    try:
        result = _run(["git", "push", remote_url, f"HEAD:{branch}"], cwd=output_dir, check=False, env=env)
        if result.returncode != 0:
            # Try setting upstream and pushing again
            result2 = _run(
                ["git", "push", "--set-upstream", remote_url, f"HEAD:{branch}"],
                cwd=output_dir,
                check=False,
                env=env,
            )
            if result2.returncode != 0:
                log.error("Git push failed: %s", result2.stderr.strip())
                return
        log.info("Git push succeeded to %s branch %s", repo, branch)
    except Exception as e:
        log.error("Git push exception: %s", e)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
