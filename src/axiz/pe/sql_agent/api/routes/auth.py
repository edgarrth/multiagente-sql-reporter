from fastapi import APIRouter, Depends, HTTPException

from axiz.pe.sql_agent.container import ApplicationContainer
from axiz.pe.sql_agent.dependencies import get_container
from axiz.pe.sql_agent.models.contracts import LoginRequest, TokenResponse

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    container: ApplicationContainer = Depends(get_container),
) -> TokenResponse:
    try:
        return await container.auth.login(request.username, request.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
