from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(container: ApplicationContainer = Depends(get_container)) -> dict:
    checks: dict[str, bool] = {}
    try:
        async with container.db.session() as session:
            result = await session.execute(text("SELECT 1"))
            checks["control_database"] = result.scalar_one() == 1
    except Exception:
        checks["control_database"] = False
    try:
        checks["business_data_database"] = await container.query_tool.ping()
    except Exception:
        checks["business_data_database"] = False
    try:
        checks["redis"] = await container.redis.ping()
    except Exception:
        checks["redis"] = False
    checks["semantic_catalog"] = bool(container.catalog.list_domains())
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ready", "checks": checks}
