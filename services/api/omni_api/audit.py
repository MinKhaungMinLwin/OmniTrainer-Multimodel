from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.models import AuditEvent


def add_audit_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    correlation_id: str,
    payload: dict | None = None,
) -> None:
    session.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
            payload=payload or {},
            occurred_at=datetime.now(timezone.utc),
        )
    )
