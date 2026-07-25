from fastapi import APIRouter, Depends

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container, verify_internal_service
from axiz.pe.sql_agent.models.contracts import TeamsMessageRequest, TeamsMessageResponse
from axiz.pe.sql_agent.services.teams_integration_service import TeamsIntegrationService

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])


@router.post(
    "/teams/messages",
    response_model=TeamsMessageResponse,
    dependencies=[Depends(verify_internal_service)],
)
async def teams_message(
    request: TeamsMessageRequest,
    container: ApplicationContainer = Depends(get_container),
) -> TeamsMessageResponse:
    service = TeamsIntegrationService(
        users=container.users,
        sessions=container.sessions,
        workflow=container.workflow,
        redis=container.redis,
    )
    return await service.handle(request)
