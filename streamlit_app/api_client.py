from __future__ import annotations

import os
from typing import Any

import httpx


class ApiClient:
    def __init__(self, token: str | None = None) -> None:
        self.base_url = os.getenv("STREAMLIT_API_BASE_URL", "http://localhost:8000")
        self.token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_session(self, title: str = "Streamlit conversation") -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/v1/sessions",
            headers=self._headers(),
            json={"title": title},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def start_run(self, session_id: str, question: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs",
            headers=self._headers(),
            json={"session_id": session_id, "question": question},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()

    def feedback(self, run_id: str, decision: str, comment: str | None = None) -> dict:
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/feedback",
            headers=self._headers(),
            json={"decision": decision, "comment": comment or None},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()
