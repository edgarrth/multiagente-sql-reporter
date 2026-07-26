from __future__ import annotations

import json
import os
from collections.abc import Iterator
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

    def create_session(self, title: str = "Nueva conversación") -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/v1/sessions",
            headers=self._headers(),
            json={"title": title},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_sessions(self) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/api/v1/sessions",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def rename_session(self, session_id: str, title: str) -> dict[str, Any]:
        response = httpx.patch(
            f"{self.base_url}/api/v1/sessions/{session_id}",
            headers=self._headers(),
            json={"title": title},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        response = httpx.delete(
            f"{self.base_url}/api/v1/sessions/{session_id}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/api/v1/sessions/{session_id}/messages",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_run(self, run_id: str) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/api/v1/agent/runs/{run_id}",
            headers=self._headers(),
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

    def stream_start_run(self, session_id: str, question: str) -> Iterator[dict[str, Any]]:
        yield from self._stream(
            "/api/v1/agent/runs/stream",
            {"session_id": session_id, "question": question},
        )

    def feedback(self, run_id: str, decision: str, comment: str | None = None) -> dict:
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/feedback",
            headers=self._headers(),
            json={"decision": decision, "comment": comment or None},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()

    def stream_feedback(
        self,
        run_id: str,
        decision: str,
        comment: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self._stream(
            f"/api/v1/agent/runs/{run_id}/feedback/stream",
            {"decision": decision, "comment": comment or None},
        )


    def export_excel(self, run_id: str) -> tuple[bytes, str]:
        response = httpx.get(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/exports/excel",
            headers=self._headers(),
            timeout=60,
        )
        response.raise_for_status()
        disposition = response.headers.get("content-disposition", "")
        filename = f"resultado-{run_id[:8]}.xlsx"
        marker = 'filename="'
        if marker in disposition:
            filename = disposition.split(marker, 1)[1].split('"', 1)[0]
        return response.content, filename

    def _stream(self, path: str, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        timeout = httpx.Timeout(connect=30, read=None, write=30, pool=30)
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        with httpx.stream(
            "POST",
            f"{self.base_url}{path}",
            headers=headers,
            json=payload,
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            event_type = "message"
            data_lines: list[str] = []
            for raw_line in response.iter_lines():
                line = raw_line.strip("\r")
                if not line:
                    if data_lines:
                        data_text = "\n".join(data_lines)
                        try:
                            data = json.loads(data_text)
                        except json.JSONDecodeError:
                            data = {"raw": data_text}
                        yield {"type": event_type, "data": data}
                    event_type = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].lstrip())
            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    data = json.loads(data_text)
                except json.JSONDecodeError:
                    data = {"raw": data_text}
                yield {"type": event_type, "data": data}
