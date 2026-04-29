"""
Authentication dependency for FastAPI endpoints.
"""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.config import settings

security = HTTPBearer(auto_error=False)


async def verify_api_secret(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """
    Dependency to verify API secret via Bearer token.
    Raises 401 if authentication fails.
    """
    # Try to get token from Authorization header
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization") or ""

    # If HTTPBearer provided credentials, use that
    if credentials:
        token = credentials.credentials
    # Otherwise parse from header
    elif auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )

    if token != settings.api_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized"
        )

    return token


async def verify_api_secret_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> bool:
    """
    Optional authentication - returns True if authenticated, False otherwise.
    """
    try:
        await verify_api_secret(request, credentials)
        return True
    except HTTPException:
        return False
