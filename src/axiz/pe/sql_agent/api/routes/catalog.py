from fastapi import APIRouter, Depends

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import UserPrincipal

router = APIRouter(prefix="/api/v1/catalog", tags=["semantic-catalog"])


@router.get("/domains")
async def list_domains(
    _: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict]:
    return container.catalog.list_domains()


@router.get("/specialists")
async def list_specialists(
    _: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> list[dict]:
    return container.specialist_registry.available_for_planning()


@router.post("/reload")
async def reload_catalog(
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    if "admin" not in principal.roles:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin role required")
    container.catalog.reload()
    specialists = container.reload_specialists()
    return {
        "status": "reloaded",
        "domains": container.catalog.list_domains(),
        "specialists": specialists,
    }


@router.get("/agent-models")
async def list_agent_models(
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    if "admin" not in principal.roles:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin role required")
    return {
        "agents": container.model_registry.list_profiles(),
        "presets": container.model_registry.list_presets(),
    }


@router.post("/agent-models/reload")
async def reload_agent_models(
    principal: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> dict:
    if "admin" not in principal.roles:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Admin role required")
    container.model_registry.reload()
    return {
        "status": "reloaded",
        "agents": container.model_registry.list_profiles(),
        "presets": container.model_registry.list_presets(),
    }
