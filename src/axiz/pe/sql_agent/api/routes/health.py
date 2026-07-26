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
        engine_health = await container.query_engine.health()
        checks["business_data_database"] = engine_health.healthy
    except Exception:
        engine_health = None
        checks["business_data_database"] = False
    try:
        checks["redis"] = await container.redis.ping()
    except Exception:
        checks["redis"] = False
    checks["semantic_catalog"] = bool(container.catalog.list_domains())
    checks["specialist_registry"] = bool(container.specialist_registry.enabled_roles())
    model_report = await container.model_validator.validate()
    checks["model_catalog"] = model_report.ready
    payload = {
        "business_data_mode": container.settings.business_data_mode,
        "query_engine": container.query_engine.capabilities.model_dump(mode="json"),
        "query_engine_health": (
            engine_health.model_dump(mode="json") if engine_health else None
        ),
        "autonomous_society": {
            "enabled": container.settings.autonomous_society_enabled,
            "enabled_specialists": sorted(
                str(role) for role in container.specialist_registry.enabled_roles()
            ),
            "budgets": container.autonomous_budget.model_dump(mode="json"),
            "adaptive_routing_enabled": (
                container.settings.autonomous_adaptive_routing_enabled
            ),
            "conditional_review_enabled": (
                container.settings.autonomous_conditional_review_enabled
            ),
            "semantic_context_projection": {
                "max_documents": container.settings.semantic_context_max_documents,
                "max_examples": container.settings.semantic_context_max_examples,
                "max_metrics": container.settings.semantic_context_max_metrics,
                "max_dimensions": container.settings.semantic_context_max_dimensions,
            },
            "agent_cache": {
                "enabled": container.settings.agent_cache_enabled,
                "namespace": container.settings.agent_cache_namespace,
            },
            "hitl_required": True,
        },
        "model_validation": {
            "mode": model_report.mode,
            "ready": model_report.ready,
            "valid": model_report.valid_count,
            "warnings": model_report.warning_count,
            "invalid": model_report.invalid_count,
            "checked_at": model_report.checked_at,
        },
        "checks": checks,
    }
    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=payload)
    return {"status": "ready", **payload}
