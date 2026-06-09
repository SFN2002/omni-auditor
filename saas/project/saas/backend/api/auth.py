"""
Omni-Auditor SaaS Dashboard — Authentication API Routes.

GitHub OAuth flow endpoints, JWT token refresh, and current user info.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.auth import (
    create_jwt_token,
    exchange_code_for_token,
    get_current_user,
    get_github_auth_url,
    get_github_user,
    verify_jwt_token,
)
from saas.backend.config import settings
from saas.backend.database import get_db
from saas.backend.models import User

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.get("/github")
async def github_login() -> RedirectResponse:
    """Initiate the GitHub OAuth flow.

    Redirects the user to GitHub's authorization page.
    """
    try:
        auth_url = get_github_auth_url()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return RedirectResponse(url=auth_url)


@router.get("/github/callback", response_model=dict)
async def github_callback(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Handle the GitHub OAuth callback.

    Exchanges the authorization code for an access token, fetches the
    user's GitHub profile, creates or updates the user record, and
    issues a JWT access token.

    Args:
        code: The authorization code from GitHub.

    Returns:
        dict with access_token, token_type, and user info.
    """
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing authorization code",
        )

    try:
        # Exchange code for token
        access_token = await exchange_code_for_token(code)

        # Fetch user profile
        github_user = await get_github_user(access_token)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GitHub API error: {exc}",
        )

    github_id = github_user.get("id")
    username = github_user.get("login")
    email = github_user.get("email")
    avatar_url = github_user.get("avatar_url")
    name = github_user.get("name") or username

    if not github_id or not username:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid GitHub user data",
        )

    # Find or create user
    result = await db.execute(
        select(User).where(User.github_id == github_id)
    )
    user = result.scalar_one_or_none()

    if user:
        # Update existing user
        user.username = username
        user.email = email
        user.avatar_url = avatar_url
        user.name = name
        user.is_active = True
        user.updated_at = datetime.now(timezone.utc)
    else:
        # Create new user
        user = User(
            github_id=github_id,
            username=username,
            email=email,
            avatar_url=avatar_url,
            name=name,
            is_active=True,
        )
        db.add(user)

    await db.commit()
    await db.refresh(user)

    # Create JWT
    jwt_token = create_jwt_token(user.id)

    return {
        "access_token": jwt_token,
        "token_type": "bearer",
        "expires_in": settings.JWT_EXPIRATION_HOURS * 3600,
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "is_active": user.is_active,
            "created_at": user.created_at,
        },
    }


@router.post("/refresh", response_model=dict)
async def refresh_token(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Refresh the JWT access token.

    Returns a new token with a fresh expiration time.
    """
    new_token = create_jwt_token(current_user.id)

    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_in": settings.JWT_EXPIRATION_HOURS * 3600,
    }


@router.get("/me", response_model=dict)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get the current authenticated user's profile."""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "name": current_user.name,
        "avatar_url": current_user.avatar_url,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at,
        "updated_at": current_user.updated_at,
    }
