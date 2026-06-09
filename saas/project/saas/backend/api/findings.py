"""
Omni-Auditor SaaS Dashboard — Finding API Routes.

Complete CRUD for findings including aggregated statistics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.auth import get_current_user
from saas.backend.database import get_db
from saas.backend.models import Finding, User
from saas.backend.schemas import FindingStats, FindingUpdate, PaginationParams

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("", response_model=dict)
async def list_findings(
    severity: Optional[str] = Query(None, description="Filter by severity"),
    status: Optional[str] = Query(None, description="Filter by status"),
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    scan_id: Optional[UUID] = Query(None, description="Filter by scan ID"),
    category: Optional[str] = Query(None, description="Filter by category"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List findings with comprehensive filtering, sorting, and pagination.

    Supports filtering by severity, status, project, scan, and category.
    Sortable by severity, status, created_at, and file_path.
    """
    query = select(Finding)

    # Apply filters
    if severity:
        query = query.where(Finding.severity == severity)

    if status:
        query = query.where(Finding.status == status)

    if project_id:
        query = query.where(Finding.project_id == project_id)

    if scan_id:
        query = query.where(Finding.scan_id == scan_id)

    if category:
        query = query.where(Finding.category == category)

    # Apply sorting
    sort_column_map = {
        "created_at": Finding.created_at,
        "severity": Finding.severity,
        "status": Finding.status,
        "file_path": Finding.file_path,
        "title": Finding.title,
        "rule_id": Finding.rule_id,
    }

    sort_col = sort_column_map.get(sort_by, Finding.created_at)
    if sort_order.lower() == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = query.offset(pagination.offset).limit(pagination.page_size)
    result = await db.execute(query)
    findings = result.scalars().all()

    items = [
        {
            "id": f.id,
            "scan_id": f.scan_id,
            "project_id": f.project_id,
            "rule_id": f.rule_id,
            "title": f.title,
            "description": f.description,
            "severity": f.severity,
            "confidence": f.confidence,
            "category": f.category,
            "file_path": f.file_path,
            "line_start": f.line_start,
            "line_end": f.line_end,
            "code_snippet": f.code_snippet,
            "remediation": f.remediation,
            "cwe_ids": f.cwe_ids,
            "owasp_category": f.owasp_category,
            "status": f.status,
            "created_at": f.created_at,
            "updated_at": f.updated_at,
        }
        for f in findings
    ]

    pages = (total + pagination.page_size - 1) // pagination.page_size

    return {
        "items": items,
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "pages": max(pages, 1),
    }


@router.get("/{finding_id}", response_model=dict)
async def get_finding(
    finding_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get detailed information about a single finding."""
    result = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = result.scalar_one_or_none()

    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finding not found",
        )

    return {
        "id": finding.id,
        "scan_id": finding.scan_id,
        "project_id": finding.project_id,
        "rule_id": finding.rule_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "category": finding.category,
        "file_path": finding.file_path,
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "code_snippet": finding.code_snippet,
        "remediation": finding.remediation,
        "cwe_ids": finding.cwe_ids,
        "owasp_category": finding.owasp_category,
        "status": finding.status,
        "created_at": finding.created_at,
        "updated_at": finding.updated_at,
    }


@router.put("/{finding_id}", response_model=dict)
async def update_finding(
    finding_id: UUID,
    update_in: FindingUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update a finding's status (open/fixed/false_positive/accepted).

    Only the status field can be updated through this endpoint.
    """
    result = await db.execute(select(Finding).where(Finding.id == finding_id))
    finding = result.scalar_one_or_none()

    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finding not found",
        )

    finding.status = update_in.status
    finding.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(finding)

    return {
        "id": finding.id,
        "scan_id": finding.scan_id,
        "project_id": finding.project_id,
        "rule_id": finding.rule_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "category": finding.category,
        "file_path": finding.file_path,
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "code_snippet": finding.code_snippet,
        "remediation": finding.remediation,
        "cwe_ids": finding.cwe_ids,
        "owasp_category": finding.owasp_category,
        "status": finding.status,
        "created_at": finding.created_at,
        "updated_at": finding.updated_at,
    }


@router.get("/stats", response_model=dict)
async def get_finding_stats(
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    scan_id: Optional[UUID] = Query(None, description="Filter by scan ID"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get aggregated finding statistics for the dashboard.

    Returns total counts, breakdowns by severity, status, and category,
    as well as counts for the last 7 and 30 days.
    """
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # Build base query
    base_query = select(Finding)
    if project_id:
        base_query = base_query.where(Finding.project_id == project_id)
    if scan_id:
        base_query = base_query.where(Finding.scan_id == scan_id)

    # Total count
    total_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = total_result.scalar_one()

    # By severity
    severity_result = await db.execute(
        select(Finding.severity, func.count())
        .group_by(Finding.severity)
        .order_by(func.count().desc())
    )
    by_severity = {row[0]: row[1] for row in severity_result.all()}

    # By status
    status_result = await db.execute(
        select(Finding.status, func.count())
        .group_by(Finding.status)
        .order_by(func.count().desc())
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # By category
    category_result = await db.execute(
        select(Finding.category, func.count())
        .group_by(Finding.category)
        .order_by(func.count().desc())
    )
    by_category = {
        (row[0] or "uncategorized"): row[1]
        for row in category_result.all()
    }

    # Recent counts (7d, 30d)
    recent_7d_result = await db.execute(
        select(func.count())
        .select_from(Finding)
        .where(Finding.created_at >= seven_days_ago)
    )
    recent_7d = recent_7d_result.scalar_one()

    recent_30d_result = await db.execute(
        select(func.count())
        .select_from(Finding)
        .where(Finding.created_at >= thirty_days_ago)
    )
    recent_30d = recent_30d_result.scalar_one()

    # Ensure all severity levels are present
    for sev in ["critical", "high", "medium", "low", "info"]:
        if sev not in by_severity:
            by_severity[sev] = 0

    # Ensure all status levels are present
    for st in ["open", "fixed", "false_positive", "accepted"]:
        if st not in by_status:
            by_status[st] = 0

    return {
        "total": total,
        "by_severity": by_severity,
        "by_status": by_status,
        "by_category": by_category,
        "recent_count_7d": recent_7d,
        "recent_count_30d": recent_30d,
    }
