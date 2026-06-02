"""
Benchmark dataset collector for Omni-Auditor.

Downloads latest release tarballs from GitHub for a hardcoded list of
high-quality Python repositories, extracts .py files (skipping test and
boilerplate directories), and writes a manifest.

Standard-library + requests only.
"""
from __future__ import annotations

import json
import os
import tarfile
import time
from io import BytesIO
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPOS = [
    "django/django",
    "fastapi/fastapi",
    "urllib3/urllib3",
    "requests/requests",
    "flask/flask",
    "scrapy/scrapy",
    "celery/celery",
    "sqlalchemy/sqlalchemy",
    "pytest-dev/pytest",
    "aio-libs/aiohttp",
    "psf/black",
    "pyca/cryptography",
    "paramiko/paramiko",
    "dateutil/dateutil",
    "pillow/Pillow",
    "benoitc/gunicorn",
    "tornadoweb/tornado",
    "pallets/click",
    "encode/httpx",
    "pypa/pip",
]

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / ".cache"
DATASETS_DIR = BASE_DIR / "datasets"
MANIFEST_PATH = DATASETS_DIR / "manifest.json"

MAX_FILES = 100
MAX_LINES = 1000

EXCLUDED_DIRS = {"tests", "test", "venv", ".venv", "__pycache__", "docs", "examples"}

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if _token := os.environ.get("GITHUB_TOKEN"):
    _HEADERS["Authorization"] = f"Bearer {_token}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _should_skip_path(archive_path: str) -> bool:
    """Return True if the archive member should be ignored."""
    parts = Path(archive_path).parts
    if any(part.lower() in EXCLUDED_DIRS for part in parts):
        return True
    if not archive_path.endswith(".py"):
        return True
    return False


def _get(url: str, stream: bool = False, max_retries: int = 1) -> requests.Response:
    """GET with 403 (rate-limit) retry."""
    retries = 0
    while True:
        resp = requests.get(url, headers=_HEADERS, stream=stream, timeout=60)
        if resp.status_code == 403 and retries < max_retries:
            print("    Rate limited (403). Sleeping 60s...")
            time.sleep(60)
            retries += 1
            continue
        resp.raise_for_status()
        return resp


def _tarball_url(owner: str, repo: str) -> str:
    """Return tarball URL: latest release if available, else default branch."""
    latest_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        data = _get(latest_url).json()
        tarball = data.get("tarball_url")
        if tarball:
            return tarball
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            pass  # No releases – fall through to default branch.
        else:
            raise
    return f"https://api.github.com/repos/{owner}/{repo}/tarball"


def _download_tarball(owner: str, repo: str, cache_path: Path) -> bytes:
    """Download tarball bytes, using on-disk cache when present."""
    if cache_path.exists():
        return cache_path.read_bytes()

    url = _tarball_url(owner, repo)
    print(f"    Fetching tarball...")
    resp = _get(url, stream=True)
    data = resp.content
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data


def _extract_py_files(data: bytes, dest_dir: Path) -> tuple[int, int]:
    """
    Extract up to MAX_FILES .py files into dest_dir.
    Files longer than MAX_LINES are truncated.
    Returns (file_count, total_lines).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_count = 0
    total_lines = 0

    with tarfile.open(fileobj=BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            if _should_skip_path(member.name):
                continue
            if file_count >= MAX_FILES:
                break

            file_obj = tar.extractfile(member)
            if file_obj is None:
                continue

            try:
                text = file_obj.read().decode("utf-8", errors="replace")
            except Exception:
                continue

            lines = text.splitlines()
            if len(lines) > MAX_LINES:
                lines = lines[:MAX_LINES]
                text = "\n".join(lines) + "\n"

            # Strip the top-level directory (e.g. 'django-django-ab12cd3/')
            parts = Path(member.name).parts
            rel_path = str(Path(*parts[1:])) if len(parts) > 1 else parts[0]

            out_path = dest_dir / rel_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")

            file_count += 1
            total_lines += len(lines)

    return file_count, total_lines


def _fetch_repo(owner: str, repo: str) -> dict:
    """Download and extract one repo. Returns a manifest entry dict."""
    repo_slug = f"{owner}/{repo}"
    dest_dir = DATASETS_DIR / repo
    cache_path = CACHE_DIR / f"{owner}_{repo}.tar.gz"

    try:
        data = _download_tarball(owner, repo, cache_path)
        file_count, total_lines = _extract_py_files(data, dest_dir)
    except Exception as exc:
        print(f"    Error: {exc}")
        return {
            "repo": repo_slug,
            "stars": 0,
            "file_count": 0,
            "total_lines": 0,
            "status": "skipped",
        }

    if file_count == 0:
        print("    No valid .py files found, skipping.")
        return {
            "repo": repo_slug,
            "stars": 0,
            "file_count": 0,
            "total_lines": 0,
            "status": "skipped",
        }

    # Try to fetch star count (best-effort)
    stars = 0
    try:
        info = _get(f"https://api.github.com/repos/{owner}/{repo}").json()
        stars = info.get("stargazers_count", 0)
    except Exception:
        pass

    print(f"    Extracted {file_count} files, {total_lines} lines.")
    return {
        "repo": repo_slug,
        "stars": stars,
        "file_count": file_count,
        "total_lines": total_lines,
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DATASETS_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    total = len(REPOS)

    for idx, repo_slug in enumerate(REPOS, start=1):
        owner, repo = repo_slug.split("/", 1)
        print(f"Downloading {idx}/{total}: {repo_slug}...")
        entry = _fetch_repo(owner, repo)
        manifest.append(entry)
        time.sleep(0.5)  # politeness delay

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ok = sum(1 for e in manifest if e["status"] == "ok")
    print(f"\nDone. {ok}/{total} repos collected.")
    print(f"Manifest written to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
