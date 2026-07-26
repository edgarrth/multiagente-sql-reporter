from fastapi import APIRouter, Depends

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import ModelValidationReport, UserPrincipal

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("/validation", response_model=ModelValidationReport)
async def get_model_validation(
    _: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> ModelValidationReport:
    return await container.model_validator.validate()


@router.post("/validation/refresh", response_model=ModelValidationReport)
async def refresh_model_validation(
    _: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> ModelValidationReport:
    return await container.model_validator.validate(force=True)
