"""Omni-Auditor GitHub App — FastAPI webhook handler."""

from __future__ import annotations

import asyncio
import hmac
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, status
from github import Github
from github.Auth import AppAuth

# Ensure project root is on sys.path for src.* imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .config import settings
from .analyzer import analyze_file
from .commenter import post_pr_comment
from .baseline import compute_drift

logger = logging.getLogger("omni_auditor_app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Omni-Auditor GitHub App")


# ── helpers ──────────────────────────────────────────────────────────────────


def _verify_signature(body: bytes, signature: str | None) -> bool:
    """Verify ``X-Hub-Signature-256`` header using HMAC-SHA256."""
    if not signature:
        return False
    secret = settings.webhook_secret.encode("utf-8")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _get_installation_auth(installation_id: int):
    """Return an AppInstallationAuth for the given installation."""
    app_auth = AppAuth(app_id=settings.app_id, private_key=settings.get_private_key())
    return await asyncio.to_thread(
        app_auth.get_installation_auth,
        installation_id,
        token_permissions={"pull_requests": "write"},
    )


async def _get_installation_client(installation_id: int) -> Github:
    """Return a PyGithub client authenticated for the installation."""
    auth = await _get_installation_auth(installation_id)
    return Github(auth=auth)


async def _get_changed_python_files(
    github_client: Github,
    repo_name: str,
    pr_number: int,
) -> list[dict[str, Any]]:
    """Return a list of changed ``.py`` files in the PR."""
    try:
        repo = await asyncio.to_thread(github_client.get_repo, repo_name)
        pr = await asyncio.to_thread(repo.get_pull, pr_number)
        files = await asyncio.to_thread(
            lambda: [
                {"filename": f.filename, "status": f.status, "raw_url": f.raw_url}
                for f in pr.get_files()
                if f.filename.endswith(".py")
            ]
        )
        return files
    except Exception as exc:
        logger.error("Failed to list changed files for %s#%d: %s", repo_name, pr_number, exc)
        return []


# ── routes ───────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    """Receive GitHub webhook events and trigger analysis."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")

    if not _verify_signature(body, signature):
        logger.warning("Webhook signature verification failed")
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)

    event_type = request.headers.get("X-GitHub-Event", "")
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.warning("Invalid JSON payload")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # Only process pull_request opened / synchronize
    if event_type != "pull_request":
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    action = payload.get("action", "")
    if action not in ("opened", "synchronize"):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Extract PR metadata
    pr_data = payload.get("pull_request", {})
    pr_number = pr_data.get("number")
    repo_data = payload.get("repository", {})
    repo_name = repo_data.get("full_name")
    head = pr_data.get("head", {})
    head_sha = head.get("sha")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")

    if not all([pr_number, repo_name, head_sha, installation_id]):
        logger.warning("Missing required PR metadata in payload")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    logger.info(
        "Processing %s action for %s#%d (sha=%s)",
        action, repo_name, pr_number, head_sha,
    )

    # ── Authenticate as the installation ────────────────────────────────────
    try:
        github_client = await _get_installation_client(int(installation_id))
    except Exception as exc:
        logger.error("GitHub App authentication failed: %s", exc)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ── Discover changed Python files ───────────────────────────────────────
    changed_files = await _get_changed_python_files(
        github_client, repo_name, int(pr_number)
    )
    if not changed_files:
        logger.info("No Python files changed in %s#%d", repo_name, pr_number)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    logger.info("Found %d changed Python file(s) in %s#%d", len(changed_files), repo_name, pr_number)

    # ── Get installation token for raw content fetch ────────────────────────
    install_auth = await _get_installation_auth(int(installation_id))
    token = await asyncio.to_thread(lambda: install_auth.token)

    # ── Analyze files concurrently ──────────────────────────────────────────
    tasks = [
        analyze_file(
            repo_name=repo_name,
            pr_number=int(pr_number),
            file_path=f["filename"],
            head_sha=head_sha,
            github_token=token,
            threshold=settings.threshold,
        )
        for f in changed_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    analysis_results: list[dict[str, Any]] = []
    for f, res in zip(changed_files, results):
        if isinstance(res, Exception):
            logger.error("Analysis failed for %s: %s", f["filename"], res)
            continue
        if res is None:
            logger.warning("No result for %s", f["filename"])
            continue
        analysis_results.append(res)

    if not analysis_results:
        logger.error("All analyses failed for %s#%d", repo_name, pr_number)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ── Compute drift against baseline (if any) ─────────────────────────────
    drift = compute_drift(repo_name, analysis_results, baseline_dir=settings.baseline_dir)

    # ── Post / update PR comment ────────────────────────────────────────────
    await post_pr_comment(github_client, repo_name, int(pr_number), analysis_results, drift)

    return Response(status_code=status.HTTP_200_OK)


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "github-app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )
