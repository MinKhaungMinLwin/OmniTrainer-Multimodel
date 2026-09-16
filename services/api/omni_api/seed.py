import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.api.omni_api.config import get_settings
from services.api.omni_api.database import build_engine, build_session_factory
from services.api.omni_api.models import Tenant, TenantMembership, User

DEV_USER_EMAIL = "owner@omni.example"
DEV_TECHNICIAN_EMAIL = "technician@omni.example"


async def seed_development_data(session_factory: async_sessionmaker[AsyncSession]) -> tuple[User, Tenant]:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
        if user is None:
            user = User(email=DEV_USER_EMAIL, display_name="Omni Owner")
            session.add(user)

        tenant = await session.scalar(select(Tenant).where(Tenant.slug == "omni-demo"))
        if tenant is None:
            tenant = Tenant(name="Omni Demo", slug="omni-demo")
            session.add(tenant)

        await session.flush()
        membership = await session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == user.id,
            )
        )
        if membership is None:
            session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role="owner"))

        technician = await session.scalar(select(User).where(User.email == DEV_TECHNICIAN_EMAIL))
        if technician is None:
            technician = User(email=DEV_TECHNICIAN_EMAIL, display_name="Omni Technician")
            session.add(technician)
            await session.flush()
        technician_membership = await session.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == technician.id,
            )
        )
        if technician_membership is None:
            session.add(TenantMembership(tenant_id=tenant.id, user_id=technician.id, role="technician"))
        await session.commit()
        return user, tenant


async def run_seed() -> None:
    settings = get_settings()
    engine = build_engine(settings.database_url)
    try:
        user, tenant = await seed_development_data(build_session_factory(engine))
        print(f"Seeded {user.email} in tenant {tenant.slug} ({tenant.id})")
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(run_seed())


if __name__ == "__main__":
    main()
