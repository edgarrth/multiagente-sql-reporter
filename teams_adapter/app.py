"""Optional Microsoft Teams channel adapter.

The adapter is isolated from the core API. Microsoft validates the Teams channel JWT and the
adapter forwards only the authenticated Teams user/conversation identity and text to FastAPI.
"""

from __future__ import annotations

import os

import httpx
import uvicorn
from fastapi import FastAPI, Request
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    Authorization,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import (
    CloudAdapter,
    JwtAuthorizationMiddleware,
    start_agent_process,
)

API_BASE_URL = os.environ["API_BASE_URL"].rstrip("/")
INTERNAL_SERVICE_KEY = os.environ["INTERNAL_SERVICE_KEY"]
TEAMS_HTTP_TIMEOUT_SECONDS = float(os.environ["TEAMS_HTTP_TIMEOUT_SECONDS"])
TEAMS_HOST = os.environ["TEAMS_HOST"]
TEAMS_PORT = int(os.environ["TEAMS_PORT"])
TEAMS_APP_TITLE = os.environ["TEAMS_APP_TITLE"]

agents_sdk_config = load_configuration_from_env(os.environ)
storage = MemoryStorage()
connection_manager = MsalConnectionManager(**agents_sdk_config)
adapter = CloudAdapter(connection_manager=connection_manager)
authorization = Authorization(storage, connection_manager, **agents_sdk_config)
agent_app = AgentApplication[TurnState](
    storage=storage,
    adapter=adapter,
    authorization=authorization,
    **agents_sdk_config,
)

app = FastAPI(title=TEAMS_APP_TITLE, version="0.11.5")
app.add_middleware(JwtAuthorizationMiddleware)


@agent_app.activity("message")
async def on_message(context: TurnContext, _state: TurnState) -> None:
    activity = context.activity
    sender = getattr(activity, "from_property", None)
    conversation = getattr(activity, "conversation", None)
    channel_user_id = (
        getattr(sender, "aad_object_id", None)
        or getattr(sender, "id", None)
        or "unknown-teams-user"
    )
    display_name = getattr(sender, "name", None)
    conversation_id = getattr(conversation, "id", None) or "unknown-conversation"
    text = (activity.text or "").strip()

    async with httpx.AsyncClient(timeout=TEAMS_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{API_BASE_URL}/api/v1/integrations/teams/messages",
            headers={"X-Internal-Service-Key": INTERNAL_SERVICE_KEY},
            json={
                "channel_user_id": channel_user_id,
                "display_name": display_name,
                "conversation_id": conversation_id,
                "text": text,
            },
        )
        response.raise_for_status()
        payload = response.json()
    await context.send_activity(payload["text"])


@app.post("/api/messages")
async def messages_handler(request: Request):
    return await start_agent_process(request, agent_app, agent_app.adapter)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "channel": "teams"}


if __name__ == "__main__":
    app.state.agent_configuration = connection_manager.get_default_connection_configuration()
    uvicorn.run(app, host=TEAMS_HOST, port=TEAMS_PORT)
