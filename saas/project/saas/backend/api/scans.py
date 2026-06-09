"""
Omni-Auditor SaaS Dashboard — Scan API Routes.

Complete CRUD for scans including SARIF export.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.auth import get_current_user
from saas.backend.celery_app import celery_app
from saas.backend.database import get_db
from saas.backend.models import Finding, Project, Scan, User
from saas.backend.schemas import PaginationParams, ScanCreate

router = APIRouter(prefix="/scans", tags=["scans"])

# ── Severity to SARIF level mapping ───────────────────────────

SARIF_LEVEL_MAP = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

CWE_TAXONOMY_REF = (
    "https://raw.githubusercontent.com/"
    "CWE-CAPEC/Software-Development-Taxonomy/main/taxonomy.json"
)


@router.get("", response_model=dict)
async def list_scans(
    project_id: Optional[UUID] = Query(None, description="Filter by project ID"),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by scan status"
    ),
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List scans with optional project and status filters.

    Returns paginated scan records sorted by creation date (newest first).
    """
    query = select(Scan)

    if project_id:
        query = query.where(Scan.project_id == project_id)

    if status_filter:
        query = query.where(Scan.status == status_filter)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = (
        query.order_by(Scan.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    result = await db.execute(query)
    scans = result.scalars().all()

    items = [
        {
            "id": s.id,
            "project_id": s.project_id,
            "status": s.status,
            "commit_sha": s.commit_sha,
            "branch": s.branch,
            "risk_score": float(s.risk_score) if s.risk_score else None,
            "risk_vector_90d": s.risk_vector_90d,
            "findings_count": s.findings_count,
            "critical_count": s.critical_count,
            "high_count": s.high_count,
            "medium_count": s.medium_count,
            "low_count": s.low_count,
            "info_count": s.info_count,
            "baseline_status": s.baseline_status,
            "triggered_by": s.triggered_by,
            "started_at": s.started_at,
            "completed_at": s.completed_at,
            "created_at": s.created_at,
        }
        for s in scans
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
async def create_scan(
    scan_in: ScanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Trigger a new scan for a project.

    Creates a pending scan record and enqueues a Celery task to run
    the Omni-Auditor analysis.
    """
    # Verify project exists and is active
    result = await db.execute(
        select(Project).where(
            Project.id == scan_in.project_id,
            Project.is_active == True,
        )
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found or inactive",
        )

    scan = Scan(
        project_id=scan_in.project_id,
        status="pending",
        commit_sha=scan_in.commit_sha,
        branch=scan_in.branch or project.default_branch,
        triggered_by=scan_in.triggered_by or "manual",
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
                scan_in.commit_sha,
            ],
        )
    except Exception as exc:
        # Celery not available — mark as failed
        scan.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to enqueue scan task: {exc}",
        )

    return {
        "id": scan.id,
        "project_id": scan.project_id,
        "status": scan.status,
        "commit_sha": scan.commit_sha,
        "branch": scan.branch,
        "triggered_by": scan.triggered_by,
        "created_at": scan.created_at,
    }


@router.get("/{scan_id}", response_model=dict)
async def get_scan(
    scan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get detailed information about a single scan."""
    result = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()

    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    return {
        "id": scan.id,
        "project_id": scan.project_id,
        "status": scan.status,
        "commit_sha": scan.commit_sha,
        "branch": scan.branch,
        "risk_score": float(scan.risk_score) if scan.risk_score else None,
        "risk_vector_90d": scan.risk_vector_90d,
        "findings_count": scan.findings_count,
        "critical_count": scan.critical_count,
        "high_count": scan.high_count,
        "medium_count": scan.medium_count,
        "low_count": scan.low_count,
        "info_count": scan.info_count,
        "baseline_status": scan.baseline_status,
        "triggered_by": scan.triggered_by,
        "started_at": scan.started_at,
        "completed_at": scan.completed_at,
        "created_at": scan.created_at,
    }


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a scan and all its associated findings."""
    result = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()

    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    await db.delete(scan)
    await db.commit()


@router.get("/{scan_id}/findings", response_model=dict)
async def get_scan_findings(
    scan_id: UUID,
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get all findings for a specific scan."""
    # Verify scan exists
    scan_result = await db.execute(select(Scan).where(Scan.id == scan_id))
    if not scan_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    count_query = select(func.count()).select_from(
        select(Finding).where(Finding.scan_id == scan_id).subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    query = (
        select(Finding)
        .where(Finding.scan_id == scan_id)
        .order_by(
            func.case(
                (Finding.severity == "critical", 1),
                (Finding.severity == "high", 2),
                (Finding.severity == "medium", 3),
                (Finding.severity == "low", 4),
                (Finding.severity == "info", 5),
                else_=6,
            ),
            Finding.created_at.desc(),
        )
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
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


@router.get("/{scan_id}/export/sarif")
async def export_sarif(
    scan_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Export scan findings as SARIF v2.1.0 JSON.

    Follows the SARIF specification with proper tool info,
    results, and code locations.
    """
    result = await db.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()

    if not scan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Scan not found",
        )

    # Get findings
    findings_result = await db.execute(
        select(Finding).where(Finding.scan_id == scan_id)
    )
    findings = findings_result.scalars().all()

    # Build SARIF results
    sarif_results: List[Dict[str, Any]] = []
    rule_index_map: Dict[str, int] = {}
    rules: List[Dict[str, Any]] = []

    for finding in findings:
        rule_id = finding.rule_id

        if rule_id not in rule_index_map:
            rule_index_map[rule_id] = len(rules)

            # Build help text
            help_text_parts = []
            if finding.description:
                help_text_parts.append(finding.description)
            if finding.remediation:
                help_text_parts.append(f"\n## Remediation\n{finding.remediation}")

            rule_def: Dict[str, Any] = {
                "id": rule_id,
                "name": finding.title,
                "shortDescription": {"text": finding.title},
                "fullDescription": {"text": finding.description or finding.title},
                "defaultConfiguration": {
                    "level": SARIF_LEVEL_MAP.get(finding.severity, "warning"),
                },
                "properties": {
                    "category": finding.category,
                    "severity": finding.severity,
                    "confidence": finding.confidence,
                    "owasp": finding.owasp_category,
                },
            }

            if help_text_parts:
                rule_def["help"] = {"text": "\n\n".join(help_text_parts)}

            if finding.cwe_ids:
                rule_def["relationships"] = [
                    {
                        "target": {
                            "id": cwe_id,
                            "index": idx,
                            "toolComponent": {"index": 0},
                        },
                        "kinds": ["relevant"],
                    }
                    for idx, cwe_id in enumerate(finding.cwe_ids)
                ]

            rules.append(rule_def)

        result_item: Dict[str, Any] = {
            "ruleId": rule_id,
            "ruleIndex": rule_index_map[rule_id],
            "level": SARIF_LEVEL_MAP.get(finding.severity, "warning"),
            "message": {"text": finding.title},
            "properties": {
                "category": finding.category,
                "confidence": finding.confidence,
                "severity": finding.severity,
            },
        }

        if finding.status == "false_positive":
            result_item["suppressions"] = [
                {
                    "kind": "external",
                    "justification": "Marked as false positive",
                }
            ]

        if finding.file_path:
            location: Dict[str, Any] = {
                "physicalLocation": {
                    "artifactLocation": {"uri": finding.file_path},
                }
            }

            if finding.line_start:
                location["physicalLocation"]["region"] = {
                    "startLine": finding.line_start,
                }
                if finding.line_end:
                    location["physicalLocation"]["region"]["endLine"] = (
                        finding.line_end
                    )
                if finding.code_snippet:
                    location["physicalLocation"]["region"]["snippet"] = {
                        "text": finding.code_snippet,
                    }

            result_item["locations"] = [location]

        sarif_results.append(result_item)

    sarif_doc: Dict[str, Any] = {
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
            "Schemata/sarif-schema-2.1.0.json"
        ),
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Omni-Auditor",
                        "informationUri": "https://github.com/omniauditor/omni-auditor",
                        "version": "1.0.0",
                        "rules": rules,
                        "taxa": [
                            {
                                "name": "CWE",
                                "shortDescription": {"text": "Common Weakness Enumeration"},
                                "informationUri": CWE_TAXONOMY_REF,
                            }
                        ],
                    }
                },
                "results": sarif_results,
                "automationDetails": {
                    "id": f"omni-auditor/{scan_id}",
                    "guid": str(scan_id),
                },
                "properties": {
                    "scanId": str(scan.id),
                    "projectId": str(scan.project_id),
                    "commitSha": scan.commit_sha,
                    "branch": scan.branch,
                    "riskScore": float(scan.risk_score) if scan.risk_score else None,
                    "triggeredBy": scan.triggered_by,
                    "baselineStatus": scan.baseline_status,
                },
            }
        ],
    }

    return sarif_doc
