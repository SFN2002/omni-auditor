"""
Omni-Auditor SaaS Dashboard — Organization API Routes.

Complete CRUD for organizations including member management.
"""

from __future__ import annotations

import re
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.auth import get_current_user
from saas.backend.database import get_db
from saas.backend.models import (
    Organization,
    OrganizationMember,
    Project,
    User,
)
from saas.backend.schemas import (
    OrganizationCreate,
    OrganizationMemberCreate,
    OrganizationWithMembers,
)

router = APIRouter(prefix="/orgs", tags=["organizations"])


def slugify(name: str) -> str:
    """Convert a name to a URL-friendly slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug[:255]


@router.get("", response_model=list)
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """List all organizations that the current user is a member of."""
    result = await db.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == current_user.id)
        .order_by(Organization.created_at.desc())
    )
    orgs = result.scalars().all()

    return [
        {
            "id": o.id,
            "name": o.name,
            "slug": o.slug,
            "github_org_id": o.github_org_id,
            "avatar_url": o.avatar_url,
            "plan": o.plan,
            "created_at": o.created_at,
            "updated_at": o.updated_at,
        }
        for o in orgs
    ]


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_organization(
    org_in: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Create a new organization.

    Auto-generates a slug from the name and adds the creator as owner.
    """
    slug = slugify(org_in.name)

    # Check for duplicate slug
    existing = await db.execute(
        select(Organization).where(Organization.slug == slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization with slug '{slug}' already exists",
        )

    org = Organization(
        name=org_in.name,
        slug=slug,
        github_org_id=org_in.github_org_id,
        avatar_url=org_in.avatar_url,
        plan="free",
    )
    db.add(org)
    await db.flush()  # Get org.id

    # Add creator as owner
    member = OrganizationMember(
        organization_id=org.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(member)
    await db.commit()
    await db.refresh(org)

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "github_org_id": org.github_org_id,
        "avatar_url": org.avatar_url,
        "plan": org.plan,
        "created_at": org.created_at,
        "updated_at": org.updated_at,
    }


@router.get("/{org_id}", response_model=dict)
async def get_organization(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get organization details including member count and project count."""
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify user is a member
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    # Get member count
    member_count_result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id
        )
    )
    member_count = len(member_count_result.scalars().all())

    # Get project count
    project_count_result = await db.execute(
        select(Project).where(
            Project.organization_id == org_id,
            Project.is_active == True,
        )
    )
    project_count = len(project_count_result.scalars().all())

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "github_org_id": org.github_org_id,
        "avatar_url": org.avatar_url,
        "plan": org.plan,
        "member_count": member_count,
        "project_count": project_count,
        "created_at": org.created_at,
        "updated_at": org.updated_at,
    }


@router.put("/{org_id}", response_model=dict)
async def update_organization(
    org_id: UUID,
    org_in: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update an organization's details.

    Only owners and admins can update an organization.
    """
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Check permissions (owner or admin)
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    member = membership.scalar_one_or_none()
    if not member or member.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can update the organization",
        )

    # Update fields
    if org_in.name:
        org.name = org_in.name
        org.slug = slugify(org_in.name)

    if org_in.github_org_id is not None:
        org.github_org_id = org_in.github_org_id

    await db.commit()
    await db.refresh(org)

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "github_org_id": org.github_org_id,
        "avatar_url": org.avatar_url,
        "plan": org.plan,
        "created_at": org.created_at,
        "updated_at": org.updated_at,
    }


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete an organization and all its associated data.

    Only owners can delete an organization.
    """
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()

    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Check ownership
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    member = membership.scalar_one_or_none()
    if not member or member.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners can delete the organization",
        )

    await db.delete(org)
    await db.commit()


# ── Member Management ─────────────────────────────────────────


@router.get("/{org_id}/members", response_model=list)
async def list_members(
    org_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list:
    """List all members of an organization."""
    # Verify membership
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    result = await db.execute(
        select(OrganizationMember, User)
        .join(User, OrganizationMember.user_id == User.id)
        .where(OrganizationMember.organization_id == org_id)
    )
    members = result.all()

    return [
        {
            "id": om.id,
            "organization_id": om.organization_id,
            "user_id": om.user_id,
            "role": om.role,
            "user": {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "name": u.name,
                "avatar_url": u.avatar_url,
                "is_active": u.is_active,
                "created_at": u.created_at,
            },
            "created_at": om.created_at,
        }
        for om, u in members
    ]


@router.post("/{org_id}/members", response_model=dict, status_code=status.HTTP_201_CREATED)
async def add_member(
    org_id: UUID,
    member_in: OrganizationMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Add a member to an organization.

    Only owners and admins can add members.
    """
    # Check permissions
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    member = membership.scalar_one_or_none()
    if not member or member.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can add members",
        )

    # Check target user exists
    user_result = await db.execute(
        select(User).where(User.id == member_in.user_id)
    )
    if not user_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check not already a member
    existing = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == member_in.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this organization",
        )

    new_member = OrganizationMember(
        organization_id=org_id,
        user_id=member_in.user_id,
        role=member_in.role,
    )
    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    return {
        "id": new_member.id,
        "organization_id": new_member.organization_id,
        "user_id": new_member.user_id,
        "role": new_member.role,
        "created_at": new_member.created_at,
    }


@router.delete("/{org_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    org_id: UUID,
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Remove a member from an organization.

    Owners can remove anyone. Members can only remove themselves.
    """
    # Check permissions
    membership = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == current_user.id,
        )
    )
    current_member = membership.scalar_one_or_none()

    if not current_member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this organization",
        )

    # Can remove self or be owner/admin removing others
    if current_user.id != user_id and current_member.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can remove other members",
        )

    # Cannot remove the last owner
    if current_user.id == user_id and current_member.role == "owner":
        owners_result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.role == "owner",
            )
        )
        owners = owners_result.scalars().all()
        if len(owners) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner",
            )

    # Find and delete target member
    target_result = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
        )
    )
    target = target_result.scalar_one_or_none()

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    await db.delete(target)
    await db.commit()
