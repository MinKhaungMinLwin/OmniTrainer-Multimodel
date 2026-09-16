from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.config import Settings
from services.api.omni_api.database import get_session
from services.api.omni_api.models import TenantMembership, User

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TenantContext:
    user: User
    tenant_id: str
    role: str


def create_access_token(settings: Settings, user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "iss": "omni-model",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    settings: Settings = request.app.state.settings
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="omni-model",
        )
        user_id = payload["sub"]
    except (InvalidTokenError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from None

    user = await session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is not active")
    return user


async def get_tenant_context(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Header(alias="X-Tenant-ID"),
) -> TenantContext:
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant access denied")
    return TenantContext(user=user, tenant_id=tenant_id, role=membership.role)


def require_roles(*allowed_roles: str):
    async def dependency(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if context.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Role does not permit this action")
        return context

    return dependency
