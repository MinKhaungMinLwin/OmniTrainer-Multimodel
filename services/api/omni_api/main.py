import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.api.omni_api.auth import create_access_token, get_current_user
from services.api.omni_api.config import Settings, get_settings
from services.api.omni_api.customers import router as customers_router
from services.api.omni_api.database import Base, build_engine, build_session_factory, get_session
from services.api.omni_api.models import TenantMembership, User
from services.api.omni_api.operations import router as operations_router
from services.api.omni_api.operations_mvp import router as operations_mvp_router
from services.api.omni_api.schemas import DevTokenRequest, TenantRead, TokenResponse, UserRead
from services.api.omni_api.seed import seed_development_data


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine = build_engine(app_settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if app_settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        if app_settings.allow_dev_auth and app_settings.auto_create_schema:
            await seed_development_data(session_factory)
        yield
        await engine.dispose()

    app = FastAPI(title="Omni Model API", version="0.1.0", lifespan=lifespan)
    app.state.settings = app_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _: Exception):
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "correlation_id": request.state.correlation_id},
        )

    @app.get("/health/live", tags=["health"])
    async def liveness():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness(session: AsyncSession = Depends(get_session)):
        await session.execute(text("SELECT 1"))
        return {"status": "ready"}

    api = APIRouter(prefix="/api/v1")

    @api.post("/dev/token", response_model=TokenResponse, tags=["development"])
    async def development_token(payload: DevTokenRequest, session: AsyncSession = Depends(get_session)):
        if not app_settings.allow_dev_auth:
            raise HTTPException(status_code=404, detail="Not found")
        user = await session.scalar(select(User).where(User.email == str(payload.email), User.is_active.is_(True)))
        if user is None:
            raise HTTPException(status_code=401, detail="Unknown development user")
        return TokenResponse(access_token=create_access_token(app_settings, user.id))

    @api.get("/me", response_model=UserRead, tags=["identity"])
    async def me(user: User = Depends(get_current_user)):
        return user

    @api.get("/tenants", response_model=list[TenantRead], tags=["identity"])
    async def tenants(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
        memberships = await session.scalars(
            select(TenantMembership)
            .options(selectinload(TenantMembership.tenant))
            .where(TenantMembership.user_id == user.id)
        )
        return [
            TenantRead(id=item.tenant.id, name=item.tenant.name, slug=item.tenant.slug, role=item.role)
            for item in memberships
        ]

    api.include_router(customers_router)
    api.include_router(operations_router)
    api.include_router(operations_mvp_router)
    app.include_router(api)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("services.api.omni_api.main:app", host="0.0.0.0", port=8001, reload=True)
