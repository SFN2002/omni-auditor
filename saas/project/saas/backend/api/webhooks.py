"""
Omni-Auditor SaaS Dashboard — Webhook API Routes.

GitHub webhook receiver with signature validation and event processing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.celery_app import celery_app
from saas.backend.config import settings
from saas.backend.database import get_db
from saas.backend.models import Project, Scan, WebhookEvent

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify the GitHub webhook signature.

    Args:
        payload: Raw request body bytes.
        signature: The X-Hub-Signature-256 header value.
        secret: The webhook secret configured in GitHub.

    Returns:
        True if the signature is valid.
    """
    if not signature or not signature.startswith("sha256="):
        return False

    expected_mac = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(signature[7:], expected_mac)


async def find_project_by_repo(
    db: AsyncSession,
    repo_full_name: str,
    repo_id: Optional[int] = None,
) -> Optional[Project]:
    """Find a project by its GitHub repository name or ID.

    Args:
        db: Async database session.
        repo_full_name: The owner/repo format string.
        repo_id: Optional GitHub repository ID.

    Returns:
        The matching Project or None.
    """
    if repo_id:
        result = await db.execute(
            select(Project).where(
                Project.github_repo_id == repo_id,
                Project.is_active == True,
            )
        )
        project = result.scalar_one_or_none()
        if project:
            return project

    result = await db.execute(
        select(Project).where(
            Project.github_repo == repo_full_name,
            Project.is_active == True,
        )
    )
    return result.scalar_one_or_none()


@router.post("/github", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def receive_github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_github_event: str = Header(None, alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(None, alias="X-GitHub-Delivery"),
) -> dict:
    """Receive and process GitHub webhook events.

    Validates the webhook signature, stores the event, and triggers
    a scan for push events to the default branch.

    Supported events: push, pull_request
    """
    if not x_github_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-GitHub-Event header",
        )

    # Read raw body for signature verification
    body = await request.body()

    # Verify signature if secret is configured
    if settings.GITHUB_WEBHOOK_SECRET:
        if not x_hub_signature_256:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing signature header",
            )
        if not verify_github_signature(
            body, x_hub_signature_256, settings.GITHUB_WEBHOOK_SECRET
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook signature",
            )

    # Parse payload
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON payload: {exc}",
        )

    # Extract repository info
    repo_data = payload.get("repository", {})
    repo_full_name = repo_data.get("full_name")
    repo_id = repo_data.get("id")

    # Find associated project
    project = await find_project_by_repo(db, repo_full_name, repo_id) if repo_full_name else None

    # Store the webhook event
    webhook_event = WebhookEvent(
        project_id=project.id if project else None,
        event_type=x_github_event,
        github_delivery=x_github_delivery,
        payload=payload,
        processed=False,
    )
    db.add(webhook_event)
    await db.commit()
    await db.refresh(webhook_event)

    # Process push events — trigger scan on default branch pushes
    if x_github_event == "push" and project:
        ref = payload.get("ref", "")
        default_branch = f"refs/heads/{project.default_branch}"

        if ref == default_branch:
            commit_sha = payload.get("after", "")

            # Create scan record
            scan = Scan(
                project_id=project.id,
                status="pending",
                commit_sha=commit_sha[:40] if commit_sha else None,
                branch=project.default_branch,
                triggered_by="webhook",
            )
            db.add(scan)
            await db.commit()
            await db.refresh(scan)

            # Enqueue Celery task
            try:
                celery_app.send_task(
                    "saas.backend.tasks.run_omni_auditor_analysis",
                    args=[
                        str(scan.id),
                        project.github_repo,
                        scan.commit_sha,
                    ],
                )
            except Exception:
                pass  # Celery not available — scan remains pending

            return {
                "status": "accepted",
                "event_id": str(webhook_event.id),
                "event_type": x_github_event,
                "scan_triggered": True,
                "scan_id": str(scan.id),
                "commit_sha": scan.commit_sha,
            }

    # Process pull_request events
    elif x_github_event == "pull_request" and project:
        action = payload.get("action", "")
        pr_data = payload.get("pull_request", {})
        pr_head = pr_data.get("head", {})
        commit_sha = pr_head.get("sha", "")
        branch = pr_head.get("ref", "")

        # Trigger scan on PR open, synchronize, or reopen
        if action in ("opened", "synchronize", "reopened"):
            scan = Scan(
                project_id=project.id,
                status="pending",
                commit_sha=commit_sha[:40] if commit_sha else None,
                branch=branch,
                triggered_by="webhook",
            )
            db.add(scan)
            await db.commit()
            await db.refresh(scan)

            try:
                celery_app.send_task(
                    "saas.backend.tasks.run_omni_auditor_analysis",
                    args=[
                        str(scan.id),
                        project.github_repo,
                        scan.commit_sha,
                    ],
                )
            except Exception:
                pass

            return {
                "status": "accepted",
                "event_id": str(webhook_event.id),
                "event_type": x_github_event,
                "scan_triggered": True,
                "scan_id": str(scan.id),
                "commit_sha": scan.commit_sha,
            }

    return {
        "status": "accepted",
        "event_id": str(webhook_event.id),
        "event_type": x_github_event,
        "scan_triggered": False,
    }
