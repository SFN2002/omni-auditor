"""
Omni-Auditor SaaS Dashboard — SQLAlchemy 2.0 Async Database Models.

All tables use UUID primary keys, proper ForeignKey constraints,
relationships, and indexes as specified in the technical specification.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


class TimestampMixin:
    """Mixin that adds created_at and updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    """A user authenticated via GitHub OAuth."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    github_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True,
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[List["OrganizationMember"]] = relationship(
        "OrganizationMember", back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_users_username", "username"),
        Index("ix_users_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username}, github_id={self.github_id})>"


class Organization(Base, TimestampMixin):
    """An organization (team) that owns projects."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True,
    )
    github_org_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, unique=True, nullable=True,
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    plan: Mapped[str] = mapped_column(String(50), default="free", nullable=False)

    members: Mapped[List["OrganizationMember"]] = relationship(
        "OrganizationMember", back_populates="organization",
        cascade="all, delete-orphan",
    )
    projects: Mapped[List["Project"]] = relationship(
        "Project", back_populates="organization",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_organizations_plan", "plan"),)

    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, name={self.name}, slug={self.slug})>"


class OrganizationMember(Base):
    """Membership of a user in an organization with a role."""

    __tablename__ = "organization_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(50), default="member", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="members",
    )
    user: Mapped["User"] = relationship("User", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uix_org_member"),
        Index("ix_org_members_org_id", "organization_id"),
        Index("ix_org_members_user_id", "user_id"),
        Index("ix_org_members_role", "role"),
    )

    def __repr__(self) -> str:
        return (
            f"<OrganizationMember(id={self.id}, org={self.organization_id}, "
            f"user={self.user_id}, role={self.role})>"
        )


class Project(Base, TimestampMixin):
    """A code repository project within an organization."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_repo: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    github_repo_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    default_branch: Mapped[str] = mapped_column(
        String(255), default="main", nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="projects",
    )
    scans: Mapped[List["Scan"]] = relationship(
        "Scan", back_populates="project",
        cascade="all, delete-orphan",
        order_by="Scan.created_at.desc()",
    )
    baselines: Mapped[List["Baseline"]] = relationship(
        "Baseline", back_populates="project",
        cascade="all, delete-orphan",
    )
    webhook_events: Mapped[List["WebhookEvent"]] = relationship(
        "WebhookEvent", back_populates="project",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uix_project_org_slug"),
        Index("ix_projects_org_id", "organization_id"),
        Index("ix_projects_is_active", "is_active"),
        Index("ix_projects_github_repo_id", "github_repo_id"),
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name={self.name}, slug={self.slug})>"


class Scan(Base):
    """A single security scan run against a project."""

    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(50), default="pending", nullable=False,
    )
    commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    risk_vector_90d: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    critical_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    high_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    medium_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    low_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    info_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    baseline_status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    triggered_by: Mapped[str] = mapped_column(
        String(50), default="manual", nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="scans")
    findings: Mapped[List["Finding"]] = relationship(
        "Finding", back_populates="scan", cascade="all, delete-orphan",
    )
    baseline: Mapped[Optional["Baseline"]] = relationship(
        "Baseline", back_populates="scan", uselist=False,
    )

    __table_args__ = (
        Index("ix_scans_project_id", "project_id"),
        Index("ix_scans_status", "status"),
        Index("ix_scans_created_at", "created_at"),
        Index("ix_scans_commit_sha", "commit_sha"),
    )

    def __repr__(self) -> str:
        return (
            f"<Scan(id={self.id}, project={self.project_id}, "
            f"status={self.status}, risk_score={self.risk_score})>"
        )


class Finding(Base):
    """A single security finding discovered during a scan."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    line_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    line_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    code_snippet: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remediation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cwe_ids: Mapped[Optional[list]] = mapped_column(ARRAY(String(50)), nullable=True)
    owasp_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="open", nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False,
    )

    scan: Mapped["Scan"] = relationship("Scan", back_populates="findings")

    __table_args__ = (
        Index("ix_findings_scan_id", "scan_id"),
        Index("ix_findings_project_id", "project_id"),
        Index("ix_findings_severity", "severity"),
        Index("ix_findings_status", "status"),
        Index("ix_findings_category", "category"),
        Index("ix_findings_rule_id", "rule_id"),
        Index("ix_findings_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Finding(id={self.id}, rule={self.rule_id}, "
            f"severity={self.severity}, status={self.status})>"
        )


class Baseline(Base):
    """A baseline snapshot of risk metrics for a project."""

    __tablename__ = "baselines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    scan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    risk_vector_90d: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    findings_distribution: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="baselines")
    scan: Mapped[Optional["Scan"]] = relationship("Scan", back_populates="baseline")

    __table_args__ = (
        Index("ix_baselines_project_id", "project_id"),
        Index("ix_baselines_scan_id", "scan_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Baseline(id={self.id}, project={self.project_id}, "
            f"risk_score={self.risk_score})>"
        )


class WebhookEvent(Base):
    """A received GitHub webhook event."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    github_delivery: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    processed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    project: Mapped[Optional["Project"]] = relationship(
        "Project", back_populates="webhook_events",
    )

    __table_args__ = (
        Index("ix_webhook_events_project_id", "project_id"),
        Index("ix_webhook_events_event_type", "event_type"),
        Index("ix_webhook_events_processed", "processed"),
        Index("ix_webhook_events_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookEvent(id={self.id}, type={self.event_type}, "
            f"processed={self.processed})>"
        )
