from fastapi import APIRouter, Depends

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, get_current_principal
from axiz.pe.sql_agent.models.contracts import ModelValidationReport, UserPrincipal
from axiz.pe.sql_agent.models.query_spec import query_spec_contracts
from axiz.pe.sql_agent.models.society import SocietyRoleContract, society_role_contracts

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


@router.get("/society-contracts", response_model=list[SocietyRoleContract])
async def get_society_contracts(
    _: UserPrincipal = Depends(get_current_principal),
) -> list[SocietyRoleContract]:
    """Return the active autonomous-society roles and their JSON Schema contracts."""
    return society_role_contracts()


@router.get("/agent-skills", response_model=dict[str, dict])
async def get_agent_skills(
    _: UserPrincipal = Depends(get_current_principal),
    container: ApplicationContainer = Depends(get_container),
) -> dict[str, dict]:
    """Return active role skills: personality, context, modes, contracts and limitations."""
    return container.agent_skill_registry.contracts()


@router.get("/query-spec-contracts", response_model=dict[str, dict])
async def get_query_spec_contracts(
    _: UserPrincipal = Depends(get_current_principal),
) -> dict[str, dict]:
    """Return schemas for query specs, patches, resolutions and SQL artifacts."""
    return query_spec_contracts()
