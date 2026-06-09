"""
Omni-Auditor SaaS Dashboard — Pydantic Schemas.

Request and response models for all API endpoints,
including validators and Config classes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Risk Point ────────────────────────────────────────────────

class RiskPoint(BaseModel):
    """A single point in a project's risk trend over time."""

    timestamp: datetime
    risk_score: float = Field(..., ge=0.0, le=1.0)
    scan_id: UUID
    commit_sha: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ── User Schemas ──────────────────────────────────────────────

class UserBase(BaseModel):
    """Base fields for a user."""

    username: str = Field(..., min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    name: Optional[str] = Field(None, max_length=255)
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    """Schema for creating a new user."""

    github_id: int = Field(..., gt=0)


class User(UserBase):
    """Full user response schema."""

    id: UUID
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Organization Member Schemas ─────────────────────────────────

class OrganizationMemberBase(BaseModel):
    """Base fields for an organization membership."""

    role: str = Field(default="member", pattern="^(owner|admin|member)$")


class OrganizationMemberCreate(OrganizationMemberBase):
    """Schema for adding a member to an organization."""

    user_id: UUID


class OrganizationMember(OrganizationMemberBase):
    """Full organization membership response schema."""

    id: UUID
    organization_id: UUID
    user_id: UUID
    user: Optional[User] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Organization Schemas ──────────────────────────────────────

class OrganizationBase(BaseModel):
    """Base fields for an organization."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)

    @field_validator("slug")
    @classmethod
    def slug_must_be_lowercase(cls, v: str) -> str:
        """Ensure slug is lowercase with only allowed characters."""
        v = v.lower().strip()
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "slug must contain only alphanumeric, hyphens, and underscores"
            )
        return v


class OrganizationCreate(OrganizationBase):
    """Schema for creating a new organization."""

    github_org_id: Optional[int] = None
    avatar_url: Optional[str] = None


class Organization(OrganizationBase):
    """Full organization response schema."""

    id: UUID
    github_org_id: Optional[int] = None
    avatar_url: Optional[str] = None
    plan: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrganizationWithMembers(Organization):
    """Organization with its member list."""

    members: List[OrganizationMember] = []


# ── Project Schemas ───────────────────────────────────────────

class ProjectBase(BaseModel):
    """Base fields for a project."""

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    github_repo: Optional[str] = Field(None, max_length=500)


class ProjectCreate(ProjectBase):
    """Schema for creating a new project."""

    organization_id: UUID
    default_branch: Optional[str] = "main"


class Project(ProjectBase):
    """Full project response schema."""

    id: UUID
    slug: str
    organization_id: UUID
    github_repo_id: Optional[int] = None
    default_branch: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectWithStats(Project):
    """Project with latest scan and risk trend data."""

    latest_scan: Optional["Scan"] = None
    risk_trend: List[RiskPoint] = []


# ── Scan Schemas ──────────────────────────────────────────────

class ScanBase(BaseModel):
    """Base fields for a scan."""

    project_id: UUID
    commit_sha: Optional[str] = Field(None, max_length=40)
    branch: Optional[str] = Field(default="main", max_length=255)


class ScanCreate(ScanBase):
    """Schema for triggering a new scan."""

    triggered_by: Optional[str] = Field(
        default="manual", pattern="^(manual|webhook|scheduled)$"
    )


class Scan(ScanBase):
    """Full scan response schema."""

    id: UUID
    status: str
    risk_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    risk_vector_90d: Optional[Dict[str, Any]] = None
    findings_count: int = Field(default=0, ge=0)
    critical_count: int = Field(default=0, ge=0)
    high_count: int = Field(default=0, ge=0)
    medium_count: int = Field(default=0, ge=0)
    low_count: int = Field(default=0, ge=0)
    info_count: int = Field(default=0, ge=0)
    baseline_status: Optional[str] = None
    triggered_by: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Finding Schemas ───────────────────────────────────────────

class FindingBase(BaseModel):
    """Base fields for a finding."""

    rule_id: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=500)
    severity: str = Field(..., pattern="^(critical|high|medium|low|info)$")
    file_path: Optional[str] = None
    line_start: Optional[int] = Field(None, ge=1)


class FindingCreate(FindingBase):
    """Schema for creating a new finding."""

    scan_id: UUID
    project_id: UUID
    description: Optional[str] = None
    confidence: Optional[str] = Field(None, pattern="^(high|medium|low)$")
    category: Optional[str] = None
    line_end: Optional[int] = Field(None, ge=1)
    code_snippet: Optional[str] = None
    remediation: Optional[str] = None
    cwe_ids: Optional[List[str]] = None
    owasp_category: Optional[str] = None
    status: Optional[str] = Field(
        default="open", pattern="^(open|fixed|false_positive|accepted)$"
    )


class Finding(FindingBase):
    """Full finding response schema."""

    id: UUID
    scan_id: UUID
    project_id: UUID
    description: Optional[str] = None
    confidence: Optional[str] = None
    category: Optional[str] = None
    line_end: Optional[int] = None
    code_snippet: Optional[str] = None
    remediation: Optional[str] = None
    cwe_ids: Optional[List[str]] = None
    owasp_category: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FindingUpdate(BaseModel):
    """Schema for updating a finding's status."""

    status: str = Field(..., pattern="^(open|fixed|false_positive|accepted)$")


# ── Finding Stats ─────────────────────────────────────────────

class FindingStats(BaseModel):
    """Aggregated finding statistics for the dashboard."""

    total: int
    by_severity: Dict[str, int]
    by_status: Dict[str, int]
    by_category: Dict[str, int]
    recent_count_7d: int
    recent_count_30d: int


# ── Webhook Schemas ───────────────────────────────────────────

class WebhookPayload(BaseModel):
    """Schema for a GitHub webhook payload."""

    ref: Optional[str] = None
    before: Optional[str] = None
    after: Optional[str] = None
    repository: Optional[Dict[str, Any]] = None
    pusher: Optional[Dict[str, Any]] = None
    pull_request: Optional[Dict[str, Any]] = None
    action: Optional[str] = None
    sender: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="allow")


class WebhookEventResponse(BaseModel):
    """Response schema for a stored webhook event."""

    id: UUID
    project_id: Optional[UUID] = None
    event_type: str
    github_delivery: Optional[str] = None
    processed: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── SARIF Export ──────────────────────────────────────────────

class SarifExport(BaseModel):
    """SARIF v2.1.0 export format for scan findings."""

    version: str = "2.1.0"
    schema_uri: str = (
        "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/"
        "Schemata/sarif-schema-2.1.0.json"
    )
    runs: List[Dict[str, Any]]


# ── Health Check ──────────────────────────────────────────────

class HealthCheck(BaseModel):
    """Health check response."""

    status: str
    version: str = "1.0.0"
    environment: str


class HealthCheckDetailed(HealthCheck):
    """Detailed health check with component statuses."""

    database: str
    redis: str
    timestamp: datetime


# ── Pagination ────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""

    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int


class PaginationParams(BaseModel):
    """Common pagination query parameters."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        """Calculate SQL offset from page and page_size."""
        return (self.page - 1) * self.page_size
