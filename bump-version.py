"""Bump the Omni-Auditor version across all project files.

Usage:
    python bump-version.py 0.1.1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def read_version_from_pyproject() -> str:
    path = Path("pyproject.toml")
    content = path.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        raise RuntimeError("Could not find version in pyproject.toml")
    return m.group(1)


def bump_pyproject(new_version: str) -> None:
    path = Path("pyproject.toml")
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        lambda m: f'{m.group(1)}"{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")


def bump_package_json(new_version: str) -> None:
    path = Path("vscode-extension/package.json")
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'^(\s*"version"\s*:\s*)"[^"]+"',
        lambda m: f'{m.group(1)}"{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")


def bump_sarif_exporter(new_version: str) -> None:
    path = Path("src/sarif_exporter.py")
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'^(_TOOL_VERSION\s*=\s*)"[^"]+"',
        lambda m: f'{m.group(1)}"{new_version}"',
        content,
        flags=re.MULTILINE,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")


def bump_action_yml(new_version: str) -> None:
    path = Path("action.yml")
    content = path.read_text(encoding="utf-8")
    # Update version strings in the embedded SARIF dicts
    content = re.sub(
        r'("name":\s*"Omni-Auditor",\s*"version":\s*)"[^"]+"',
        lambda m: f'{m.group(1)}"{new_version}"',
        content,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")


def bump_dashboard(new_version: str) -> None:
    path = Path("tools/dashboard.py")
    content = path.read_text(encoding="utf-8")
    content = re.sub(
        r'(Omni-Auditor\s+v)[\d.]+',
        lambda m: f'{m.group(1)}{new_version}',
        content,
    )
    path.write_text(content, encoding="utf-8")
    print(f"  -> {path}")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <new-version>")
        return 1

    new_version = sys.argv[1]
    current = read_version_from_pyproject()
    print(f"Bumping version: {current} -> {new_version}")

    bump_pyproject(new_version)
    bump_package_json(new_version)
    bump_sarif_exporter(new_version)
    bump_action_yml(new_version)
    bump_dashboard(new_version)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
