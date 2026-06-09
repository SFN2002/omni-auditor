"""
Omni-Auditor SaaS Dashboard — Project API Routes.

Complete CRUD for projects including risk trend and baseline endpoints.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from saas.backend.auth import get_current_user
from saas.backend.database import get_db
from saas.backend.models import Baseline, Project, Scan, User
from saas.backend.schemas import (
    PaginationParams,
    ProjectCreate,
    ProjectWithStats,
    RiskPoint,
)

router = APIRouter(prefix="/projects", tags=["projects"])


def slugify(name: str) -> str:
    """Convert a project name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug[:255]


@router.get("", response_model=dict)
async def list_projects(
    org_id: Optional[UUID] = Query(None, description="Filter by organization ID"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List all projects with optional organization filter and pagination.

    Returns a paginated list of active projects sorted by creation date.
    """
    query = select(Project).where(Project.is_active == True)

    if org_id:
        query = query.where(Project.organization_id == org_id)

    # Count total
    count_query = select(func.count()).select_from(
        query.subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Fetch paginated results
    query = (
        query.order_by(Project.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    result = await db.execute(query)
    projects = result.scalars().all()

    items = [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "organization_id": p.organization_id,
            "description": p.description,
            "github_repo": p.github_repo,
            "default_branch": p.default_branch,
            "is_active": p.is_active,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in projects
    ]

    pages = (total + pagination.page_size - 1) // pagination.page_size

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "pages": max(pages, 1),
    }


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a new project within an organization.

    Auto-generates a slug from the project name and checks for uniqueness.
    """
    slug = slugify(project_in.name)

    # Check for duplicate slug within org
    existing = await db.execute(
        select(Project).where(
            Project.organization_id == project_in.organization_id,
            Project.slug == slug,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with slug '{slug}' already exists in this organization",
        )

    project = Project(
        organization_id=project_in.organization_id,
        name=project_in.name,
        slug=slug,
        description=project_in.description,
        github_repo=project_in.github_repo,
        default_branch=project_in.default_branch or "main",
        is_active=True,
    )

    db.add(project)
    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "organization_id": project.organization_id,
        "description": project.description,
        "github_repo": project.github_repo,
        "default_branch": project.default_branch,
        "is_active": project.is_active,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


@router.get("/{project_id}", response_model=dict)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get a single project with its latest scan and risk trend data.

    Includes the most recent completed scan and a 30-point risk trend.
    """
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.scans))
        .where(Project.id == project_id, Project.is_active == True)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Get latest completed scan
    latest_scan = None
    if project.scans:
        completed = [s for s in project.scans if s.status == "completed"]
        if completed:
            latest_scan = completed[0]

    # Get risk trend (last 30 scans)
    trend_result = await db.execute(
        select(Scan)
        .where(
            Scan.project_id == project_id,
            Scan.status == "completed",
            Scan.risk_score.isnot(None),
        )
        .order_by(Scan.created_at.desc())
        .limit(30)
    )
    scans_for_trend = trend_result.scalars().all()

    risk_trend = [
        RiskPoint(
            timestamp=s.created_at,
            risk_score=float(s.risk_score) if s.risk_score else 0.0,
            scan_id=s.id,
            commit_sha=s.commit_sha,
        )
        for s in reversed(scans_for_trend)
    ]

    response = {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "organization_id": project.organization_id,
        "description": project.description,
        "github_repo": project.github_repo,
        "github_repo_id": project.github_repo_id,
        "default_branch": project.default_branch,
        "is_active": project.is_active,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "latest_scan": None,
        "risk_trend": risk_trend,
    }

    if latest_scan:
        response["latest_scan"] = {
            "id": latest_scan.id,
            "project_id": latest_scan.project_id,
            "status": latest_scan.status,
            "commit_sha": latest_scan.commit_sha,
            "branch": latest_scan.branch,
            "risk_score": float(latest_scan.risk_score) if latest_scan.risk_score else None,
            "risk_vector_90d": latest_scan.risk_vector_90d,
            "findings_count": latest_scan.findings_count,
            "critical_count": latest_scan.critical_count,
            "high_count": latest_scan.high_count,
            "medium_count": latest_scan.medium_count,
            "low_count": latest_scan.low_count,
            "info_count": latest_scan.info_count,
            "baseline_status": latest_scan.baseline_status,
            "triggered_by": latest_scan.triggered_by,
            "started_at": latest_scan.started_at,
            "completed_at": latest_scan.completed_at,
            "created_at": latest_scan.created_at,
        }

    return response


@router.put("/{project_id}", response_model=dict)
async def update_project(
    project_id: UUID,
    project_in: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update an existing project.

    Only updates provided fields; others remain unchanged.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.is_active == True)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Update fields
    if project_in.name and project_in.name != project.name:
        project.name = project_in.name
        project.slug = slugify(project_in.name)

    if project_in.description is not None:
        project.description = project_in.description

    if project_in.github_repo is not None:
        project.github_repo = project_in.github_repo

    if project_in.default_branch is not None:
        project.default_branch = project_in.default_branch

    await db.commit()
    await db.refresh(project)

    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "organization_id": project.organization_id,
        "description": project.description,
        "github_repo": project.github_repo,
        "default_branch": project.default_branch,
        "is_active": project.is_active,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Soft-delete a project by setting is_active to False."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.is_active == True)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    project.is_active = False
    await db.commit()


@router.get("/{project_id}/risk-trend", response_model=list)
async def get_risk_trend(
    project_id: UUID,
    points: int = Query(30, ge=1, le=100, description="Number of data points"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """Return risk score trend data for a project.

    Returns up to `points` historical risk scores from completed scans.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.is_active == True)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    trend_result = await db.execute(
        select(Scan)
        .where(
            Scan.project_id == project_id,
            Scan.status == "completed",
            Scan.risk_score.isnot(None),
        )
        .order_by(Scan.created_at.desc())
        .limit(points)
    )
    scans = trend_result.scalars().all()

    return [
        {
            "timestamp": s.created_at,
            "risk_score": float(s.risk_score) if s.risk_score else 0.0,
            "scan_id": s.id,
            "commit_sha": s.commit_sha,
        }
        for s in reversed(scans)
    ]


@router.get("/{project_id}/baseline", response_model=dict)
async def get_baseline(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return baseline comparison data for a project.

    Compares the latest scan against the stored baseline to determine
    whether the project has improved, degraded, or stayed stable.
    """
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.is_active == True)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Get latest baseline
    baseline_result = await db.execute(
        select(Baseline)
        .where(Baseline.project_id == project_id)
        .order_by(Baseline.created_at.desc())
        .limit(1)
    )
    baseline = baseline_result.scalar_one_or_none()

    # Get latest completed scan
    scan_result = await db.execute(
        select(Scan)
        .where(
            Scan.project_id == project_id,
            Scan.status == "completed",
        )
        .order_by(Scan.created_at.desc())
        .limit(1)
    )
    latest_scan = scan_result.scalar_one_or_none()

    if not baseline and not latest_scan:
        return {
            "has_baseline": False,
            "has_scan": False,
            "status": "no_data",
            "message": "No baseline or completed scan data available.",
        }

    if not baseline:
        # Create baseline from latest scan
        baseline = Baseline(
            project_id=project_id,
            scan_id=latest_scan.id if latest_scan else None,
            risk_score=latest_scan.risk_score if latest_scan else None,
            risk_vector_90d=latest_scan.risk_vector_90d if latest_scan else None,
            findings_distribution={
                "critical": latest_scan.critical_count if latest_scan else 0,
                "high": latest_scan.high_count if latest_scan else 0,
                "medium": latest_scan.medium_count if latest_scan else 0,
                "low": latest_scan.low_count if latest_scan else 0,
                "info": latest_scan.info_count if latest_scan else 0,
            },
        )
        db.add(baseline)
        await db.commit()

        return {
            "has_baseline": True,
            "has_scan": True,
            "status": "created",
            "message": "Initial baseline created from latest scan.",
            "baseline_risk_score": float(baseline.risk_score) if baseline.risk_score else None,
            "current_risk_score": float(latest_scan.risk_score) if latest_scan and latest_scan.risk_score else None,
            "baseline_distribution": baseline.findings_distribution,
        }

    if not latest_scan:
        return {
            "has_baseline": True,
            "has_scan": False,
            "status": "no_current_scan",
            "message": "Baseline exists but no completed scan found.",
            "baseline_risk_score": float(baseline.risk_score) if baseline.risk_score else None,
        }

    # Compare baseline vs current
    baseline_risk = float(baseline.risk_score) if baseline.risk_score else 0.0
    current_risk = float(latest_scan.risk_score) if latest_scan.risk_score else 0.0

    if baseline_risk == 0:
        pct_change = 0.0
    else:
        pct_change = (current_risk - baseline_risk) / baseline_risk

    # Determine baseline status
    if latest_scan.baseline_status:
        status_label = latest_scan.baseline_status
    elif pct_change < -0.10:
        status_label = "improved"
    elif pct_change > 0.10:
        status_label = "degraded"
    else:
        status_label = "stable"

    # Check for fractured (critical/high increase > 50%)
    baseline_dist = baseline.findings_distribution or {}
    if isinstance(baseline_dist, str):
        import json
        baseline_dist = json.loads(baseline_dist)
    baseline_high = baseline_dist.get("critical", 0) + baseline_dist.get("high", 0)
    current_high = latest_scan.critical_count + latest_scan.high_count
    if baseline_high > 0 and (current_high - baseline_high) / baseline_high > 0.5:
        status_label = "fractured"

    return {
        "has_baseline": True,
        "has_scan": True,
        "status": status_label,
        "baseline_risk_score": baseline_risk,
        "current_risk_score": current_risk,
        "percent_change": round(pct_change * 100, 2),
        "baseline_distribution": baseline_dist,
        "current_distribution": {
            "critical": latest_scan.critical_count,
            "high": latest_scan.high_count,
            "medium": latest_scan.medium_count,
            "low": latest_scan.low_count,
            "info": latest_scan.info_count,
        },
        "latest_scan_id": latest_scan.id,
        "latest_commit_sha": latest_scan.commit_sha,
    }
