#!/usr/bin/env python3
"""Collect a labelled benchmark dataset from public GitHub repositories.

The framework downloads Python files from curated vulnerable and benign
repositories, labels them, and caches everything locally so the benchmark can
be rerun without hitting GitHub's API repeatedly.

Usage
-----
    export GITHUB_TOKEN=ghp_xxx  # optional, raises rate limit
    python benchmarks/collect_dataset.py --output benchmarks/data/dataset.json

Dataset design
--------------
* Vulnerable files are sourced from repositories that collect CVE snippets and
  vulnerable code patterns.
* Benign files are sourced from highly-starred, well-maintained Python projects.
* Files are labelled at the file level: ``VULNERABLE`` or ``BENIGN``.
* Files are cached in ``benchmarks/data/cache/``; the JSON metadata references
  cache paths, not raw content, to keep the dataset file small.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT: Path = Path("benchmarks/data/dataset.json")
DEFAULT_CACHE_DIR: Path = Path("benchmarks/data/cache")
MIN_DATASET_SIZE: int = 500
TARGET_PER_CLASS: int = 260  # slightly above 250 to allow for download failures

VULNERABLE_SOURCES: list[dict[str, Any]] = [
    {
        "owner": "snoopysecurity",
        "repo": "Vulnerable-Code-Snippets",
        "branch": "master",
        "label": "VULNERABLE",
        "max_files": 300,
    },
    {
        "owner": "OWASP",
        "repo": "Vulnerable-Web-Applications-Directory",
        "branch": "master",
        "label": "VULNERABLE",
        "max_files": 50,
    },
]

BENIGN_SOURCES: list[dict[str, Any]] = [
    {
        "owner": "django",
        "repo": "django",
        "branch": "main",
        "label": "BENIGN",
        "max_files": 90,
    },
    {
        "owner": "pallets",
        "repo": "flask",
        "branch": "main",
        "label": "BENIGN",
        "max_files": 60,
    },
    {
        "owner": "psf",
        "repo": "requests",
        "branch": "main",
        "label": "BENIGN",
        "max_files": 50,
    },
    {
        "owner": "numpy",
        "repo": "numpy",
        "branch": "main",
        "label": "BENIGN",
        "max_files": 60,
    },
    {
        "owner": "python",
        "repo": "cpython",
        "branch": "main",
        "path": "Lib",
        "label": "BENIGN",
        "max_files": 60,
    },
]


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------


def _session() -> requests.Session:
    session = requests.Session()
    token = os.getenv("GITHUB_TOKEN")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["User-Agent"] = "omni-auditor-benchmark"
    return session


def _rate_limit_wait(response: requests.Response) -> None:
    """Pause when GitHub rate limit is approaching."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", "1"))
    if remaining < 5:
        reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
        wait = max(reset_at - int(time.time()), 0) + 1
        print(f"Rate limit low ({remaining}); sleeping {wait}s ...")
        time.sleep(wait)


def _list_tree(
    session: requests.Session,
    owner: str,
    repo: str,
    branch: str,
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Recursively list Python files under a repository path using the git tree API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}"
    params: dict[str, str | int] = {"recursive": "1"}
    if path:
        params["path"] = path

    response = session.get(url, params=params, timeout=60)
    _rate_limit_wait(response)
    response.raise_for_status()
    data = response.json()

    files = [
        item
        for item in data.get("tree", [])
        if item.get("type") == "blob" and item.get("path", "").endswith(".py")
    ]
    return files


def _download_raw(
    session: requests.Session,
    owner: str,
    repo: str,
    branch: str,
    path: str,
) -> str | None:
    """Download the raw text of a file from GitHub."""
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    response = session.get(url, timeout=60)
    _rate_limit_wait(response)
    if response.status_code != 200:
        return None
    # Reject binary-looking content.
    if b"\0" in response.content:
        return None
    try:
        return response.content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _cache_key(owner: str, repo: str, branch: str, path: str) -> str:
    payload = f"{owner}/{repo}/{branch}/{path}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Dataset construction
# ---------------------------------------------------------------------------


def _collect_from_source(
    session: requests.Session,
    cache_dir: Path,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    """Download up to *max_files* Python files from a single source."""
    owner = source["owner"]
    repo = source["repo"]
    branch = source["branch"]
    label = source["label"]
    max_files = source["max_files"]
    root_path = source.get("path")

    print(f"Listing {owner}/{repo} ...")
    tree_files = _list_tree(session, owner, repo, branch, root_path)
    # Prefer smaller files (more likely to be human-inspectable snippets).
    tree_files.sort(key=lambda item: item.get("size", float("inf")))

    records: list[dict[str, Any]] = []
    for item in tree_files:
        if len(records) >= max_files:
            break
        path = item["path"]
        key = _cache_key(owner, repo, branch, path)
        cache_path = cache_dir / f"{key}.py"
        meta_path = cache_dir / f"{key}.json"

        if cache_path.exists() and meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            records.append(meta)
            continue

        content = _download_raw(session, owner, repo, branch, path)
        if content is None:
            continue
        if len(content) < 50 or len(content) > 50_000:
            continue

        cache_path.write_text(content, encoding="utf-8")
        meta = {
            "id": key,
            "source": f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
            "cache_path": str(cache_path.relative_to(cache_dir.parent)),
            "label": label,
            "owner": owner,
            "repo": repo,
            "path": path,
            "size": len(content),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        records.append(meta)
        print(f"  [{len(records):03d}/{max_files:03d}] {path}")

    return records


def _balance(records: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """Cap each class to *target* samples."""
    by_label: dict[str, list[dict[str, Any]]] = {"VULNERABLE": [], "BENIGN": []}
    for r in records:
        by_label.setdefault(r["label"], []).append(r)
    balanced: list[dict[str, Any]] = []
    for label, items in by_label.items():
        balanced.extend(items[:target])
        print(f"  {label}: {len(balanced) - (len(balanced) - len(items[:target]))} -> {len(items[:target])}")
    return balanced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect a labelled benchmark dataset from GitHub.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output JSON path.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Download cache directory.")
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    session = _session()
    records: list[dict[str, Any]] = []

    print("Collecting vulnerable sources ...")
    for source in VULNERABLE_SOURCES:
        records.extend(_collect_from_source(session, args.cache_dir, source))

    print("Collecting benign sources ...")
    for source in BENIGN_SOURCES:
        records.extend(_collect_from_source(session, args.cache_dir, source))

    records = _balance(records, TARGET_PER_CLASS)

    dataset = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_files": len(records),
        "vulnerable": sum(1 for r in records if r["label"] == "VULNERABLE"),
        "benign": sum(1 for r in records if r["label"] == "BENIGN"),
        "records": records,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)

    print(f"\nDataset saved to {args.output}")
    print(f"  Total files: {dataset['total_files']}")
    print(f"  Vulnerable : {dataset['vulnerable']}")
    print(f"  Benign     : {dataset['benign']}")
    if dataset["total_files"] < MIN_DATASET_SIZE:
        print(f"WARNING: dataset is smaller than the {MIN_DATASET_SIZE} file target.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
