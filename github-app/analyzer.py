"""Run Omni-Auditor analysis on a single PR file."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def analyze_file(
    repo_name: str,
    pr_number: int,
    file_path: str,
    head_sha: str,
    github_token: str,
    threshold: float = 0.7,
) -> dict[str, Any] | None:
    """Fetch a single changed file from GitHub and run Omni-Auditor on it.

    Parameters
    ----------
    repo_name:
        ``owner/repo`` full name.
    pr_number:
        Pull request number.
    file_path:
        Path to the file inside the repository.
    head_sha:
        HEAD commit SHA of the PR branch.
    github_token:
        Installation access token.
    threshold:
        Risk tier threshold forwarded to ``--threshold``.

    Returns
    -------
    dict | None
        Parsed JSON report from Omni-Auditor, or ``None`` on failure.
    """
    # ── 1. Fetch raw file content from GitHub ────────────────────────────────
    url = f"https://api.github.com/repos/{repo_name}/contents/{file_path}?ref={head_sha}"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
            resp.raise_for_status()
            source_code = resp.text
        except Exception as exc:
            logger.error("Failed to fetch %s@%s: %s", file_path, head_sha, exc)
            return None

    # ── 2. Write to a temporary file ─────────────────────────────────────────
    temp_path = Path(tempfile.gettempdir()) / f"omni-auditor-{repo_name.replace('/', '-')}-{pr_number}-{Path(file_path).name}"
    try:
        temp_path.write_text(source_code, encoding="utf-8")
    except Exception as exc:
        logger.error("Failed to write temp file %s: %s", temp_path, exc)
        return None

    # ── 3. Run Omni-Auditor as a subprocess ──────────────────────────────────
    # We must run from the project root so ``python -m src.main`` resolves.
    project_root = Path(__file__).resolve().parent.parent
    cmd = [
        "python", "-m", "src.main", str(temp_path),
        "--json", "--threshold", str(threshold),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            logger.error(
                "Omni-Auditor exited %d for %s: stderr=%s",
                proc.returncode, file_path, stderr[:500],
            )
            return None
    except asyncio.TimeoutError:
        logger.error("Omni-Auditor timed out for %s", file_path)
        return None
    except Exception as exc:
        logger.error("Failed to spawn Omni-Auditor for %s: %s", file_path, exc)
        return None
    finally:
        # Best-effort cleanup
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ── 4. Parse JSON ────────────────────────────────────────────────────────
    # The CLI emits the compact JSON payload to stdout and also writes
    # output.json in the project root. We try stdout first.
    report: dict[str, Any] | None = None
    try:
        # Find the first JSON object in stdout
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start != -1 and end != -1 and end > start:
            report = json.loads(stdout[start : end + 1])
    except json.JSONDecodeError:
        logger.debug("Could not parse JSON from stdout for %s", file_path)

    if report is None:
        # Fallback: read the output.json written by src.main
        try:
            output_json = project_root / "output.json"
            if output_json.exists():
                report = json.loads(output_json.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Fallback output.json read failed for %s: %s", file_path, exc)
            return None

    if report is None:
        logger.error("No parsable JSON output for %s", file_path)
        return None

    # ── 5. Normalise into a lightweight result dict ──────────────────────────
    return {
        "file_path": file_path,
        "risk_score": float(report.get("unified_risk_score", 0.0)),
        "risk_tier": str(report.get("risk_tier", "UNKNOWN")),
        "findings_count": len(report.get("security_findings", [])),
        "function_metrics": report.get("per_function_metrics", []),
        "security_findings": report.get("security_findings", []),
        "raw_report": report,
    }
