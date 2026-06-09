"""
Omni-Auditor SaaS Dashboard — Authentication Module.

GitHub OAuth2 flow implementation, JWT token creation/validation,
and FastAPI dependency for protected routes.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from saas.backend.config import settings
from saas.backend.database import get_db
from saas.backend.models import User

# ── Constants ─────────────────────────────────────────────────

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_API = "https://api.github.com/user"
GITHUB_EMAILS_API = "https://api.github.com/user/emails"

security = HTTPBearer(auto_error=False)


# ── GitHub OAuth Flow ─────────────────────────────────────────


def get_github_auth_url(state: Optional[str] = None) -> str:
    """Build the GitHub OAuth authorization URL.

    Args:
        state: Optional CSRF protection state parameter.

    Returns:
        Full GitHub authorization URL for redirect.
    """
    if not settings.GITHUB_CLIENT_ID:
        raise RuntimeError("GITHUB_CLIENT_ID is not configured")

    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.FRONTEND_URL}/api/v1/auth/github/callback",
        "scope": "read:user user:email repo",
        "allow_signup": "true",
    }
    if state:
        params["state"] = state

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GITHUB_AUTH_URL}?{query}"


async def exchange_code_for_token(code: str) -> str:
    """Exchange a GitHub authorization code for an access token.

    Args:
        code: The authorization code from GitHub callback.

    Returns:
        The GitHub access token.

    Raises:
        HTTPException: If the token exchange fails.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": f"{settings.FRONTEND_URL}/api/v1/auth/github/callback",
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to exchange code for token",
        )

    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        error = data.get("error_description", "Unknown error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"GitHub token exchange failed: {error}",
        )

    return access_token


async def get_github_user(token: str) -> dict:
    """Fetch the authenticated user's profile from GitHub API.

    Args:
        token: GitHub access token.

    Returns:
        User profile dictionary containing id, login, email, etc.

    Raises:
        HTTPException: If the GitHub API request fails.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        user_response = await client.get(GITHUB_USER_API, headers=headers)
        if user_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Failed to fetch GitHub user profile",
            )
        user_data = user_response.json()

        # Fetch primary email if not public
        if not user_data.get("email"):
            emails_response = await client.get(GITHUB_EMAILS_API, headers=headers)
            if emails_response.status_code == 200:
                emails = emails_response.json()
                primary = next(
                    (e for e in emails if e.get("primary") and e.get("verified")),
                    next((e for e in emails if e.get("primary")), None),
                )
                if primary:
                    user_data["email"] = primary.get("email")

    return user_data


# ── JWT Handling ──────────────────────────────────────────────


def create_jwt_token(user_id: UUID) -> str:
    """Create a JWT access token for the given user.

    Args:
        user_id: The UUID of the authenticated user.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=settings.JWT_EXPIRATION_HOURS)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": expire,
        "type": "access",
        "jti": secrets.token_hex(16),
    }

    token = jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return token


def verify_jwt_token(token: str) -> UUID:
    """Verify and decode a JWT access token.

    Args:
        token: The JWT string to verify.

    Returns:
        The user_id UUID embedded in the token.

    Raises:
        HTTPException: If the token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    try:
        return UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        )


# ── FastAPI Dependencies ──────────────────────────────────────


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency that resolves the current authenticated user.

    Args:
        credentials: HTTP Bearer token from the Authorization header.
        db: Async database session.

    Returns:
        The authenticated User model instance.

    Raises:
        HTTPException: If authentication fails.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = verify_jwt_token(credentials.credentials)

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    return user


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Alias for get_current_user — strict authentication dependency.

    Args:
        credentials: HTTP Bearer token from the Authorization header.
        db: Async database session.

    Returns:
        The authenticated User model instance.
    """
    return await get_current_user(credentials, db)
