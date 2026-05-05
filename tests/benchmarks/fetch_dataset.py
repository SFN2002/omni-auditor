"""
Fetch 50-100 popular Python repositories from GitHub for benchmarking.

Uses the GitHub REST API (requests + json) to:
1. Search for Python repos with >100 stars.
2. Filter repos that have >50 Python files (excluding .venv, __pycache__, tests/).
3. Download valid .py files to tests/benchmarks/dataset/downloaded/.
4. Cache repo metadata in tests/benchmarks/dataset/repos.json.

Set the GITHUB_TOKEN environment variable to avoid rate limits.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

BENCHMARK_DIR = Path(__file__).parent
DATASET_DIR = BENCHMARK_DIR / "dataset" / "downloaded"
REPOS_JSON = BENCHMARK_DIR / "dataset" / "repos.json"

_API_BASE = "https://api.github.com"
_RAW_BASE = "https://raw.githubusercontent.com"

_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
if _token := os.environ.get("GITHUB_TOKEN"):
    _HEADERS["Authorization"] = f"Bearer {_token}"


def _get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Perform an authenticated GET against the GitHub API."""
    resp = requests.get(url, headers=_HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def search_repos(per_page: int = 100, pages: int = 1) -> list[dict[str, Any]]:
    """Return repositories matching the search criteria."""
    items: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        data = _get(
            f"{_API_BASE}/search/repositories",
            {
                "q": "language:python stars:>100",
                "sort": "stars",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            },
        )
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < per_page:
            break
    return items


def list_repo_tree(owner: str, repo: str, branch: str) -> list[dict[str, Any]]:
    """Return the recursive git tree for a repo branch."""
    url = f"{_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}"
    data = _get(url, {"recursive": "1"})
    return data.get("tree", [])


def is_valid_py_path(path: str) -> bool:
    """Return True for .py files that are not in excluded directories."""
    if not path.endswith(".py"):
        return False
    lower = path.lower()
    excluded = (
        ".venv",
        "venv/",
        "__pycache__",
        "/tests/",
        "/test/",
        "tests/",
        "test/",
    )
    return not any(ex in lower for ex in excluded)


def download_raw(
    owner: str, repo: str, branch: str, path: str, dest: Path
) -> None:
    """Download a single file from raw.githubusercontent.com."""
    url = f"{_RAW_BASE}/{owner}/{repo}/{branch}/{path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(resp.text, encoding="utf-8")


def main() -> None:
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_JSON.parent.mkdir(parents=True, exist_ok=True)

    target_repo_count = 50
    max_repo_count = 100
    min_py_files = 50

    print(f"Searching GitHub for popular Python repos (target: {target_repo_count}-{max_repo_count})...")
    candidates = search_repos(per_page=100, pages=2)
    print(f"  Search returned {len(candidates)} candidates.")

    selected_repos: list[dict[str, Any]] = []

    for idx, repo in enumerate(candidates, 1):
        if len(selected_repos) >= max_repo_count:
            break

        owner = repo["owner"]["login"]
        name = repo["name"]
        stars = repo.get("stargazers_count", 0)
        branch = repo.get("default_branch", "main")

        print(f"[{idx}/{len(candidates)}] Checking {owner}/{name} ({stars} ⭐) ...")

        try:
            tree = list_repo_tree(owner, name, branch)
        except requests.HTTPError as exc:
            # Try fallback branch if default fails
            if branch != "master":
                try:
                    tree = list_repo_tree(owner, name, "master")
                    branch = "master"
                except requests.HTTPError:
                    print(f"    Skipped: unable to list tree ({exc.response.status_code if exc.response else '?'})")
                    continue
            else:
                print(f"    Skipped: unable to list tree ({exc.response.status_code if exc.response else '?'})")
                continue

        py_entries = [item for item in tree if is_valid_py_path(item.get("path", ""))]
        if len(py_entries) < min_py_files:
            print(f"    Only {len(py_entries)} Python files — skipping.")
            continue

        print(f"    Downloading {len(py_entries)} .py files ...")
        downloaded = 0
        for entry in py_entries:
            path = entry["path"]
            dest = DATASET_DIR / f"{owner}_{name}" / path
            if dest.exists():
                downloaded += 1
                continue
            try:
                download_raw(owner, name, branch, path, dest)
                downloaded += 1
            except Exception as exc:
                print(f"      Failed {path}: {exc}")

        selected_repos.append(
            {
                "owner": owner,
                "name": name,
                "stars": stars,
                "branch": branch,
                "python_files": len(py_entries),
                "files_downloaded": downloaded,
            }
        )
        print(f"    Saved {downloaded}/{len(py_entries)} files.")

        # Politeness delay between repos to respect API rate limits.
        time.sleep(0.6)

    REPOS_JSON.write_text(json.dumps(selected_repos, indent=2), encoding="utf-8")
    total_downloaded = sum(r["files_downloaded"] for r in selected_repos)
    print(f"\nDone. Selected {len(selected_repos)} repos, {total_downloaded} files total.")
    print(f"Cache written to {REPOS_JSON}")


if __name__ == "__main__":
    main()
