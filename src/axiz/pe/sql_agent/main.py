from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from axiz.pe.sql_agent.api.routes import agent, auth, catalog, health, integrations, models, sessions
from axiz.pe.sql_agent.config import get_settings
from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.core.logging import configure_logging
from axiz.pe.sql_agent.core.request_logging import RequestLoggingMiddleware

settings = get_settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = ApplicationContainer(settings)
    app.state.container = container
    await container.start()
    try:
        yield
    finally:
        await container.close()


app = FastAPI(
    title=settings.app_name,
    version="0.11.3",
    description="Coordinator-led governed autonomous Text-to-SQL society with human approval",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    RequestLoggingMiddleware,
    enabled=settings.log_http_requests,
    log_health_checks=settings.log_health_checks,
)

for router in (
    health.router,
    auth.router,
    sessions.router,
    catalog.router,
    models.router,
    agent.router,
    integrations.router,
):
    app.include_router(router)
