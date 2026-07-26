from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

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

    def start_run(
        self, session_id: str, question: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        key = idempotency_key or str(uuid4())
        headers = self._headers()
        headers["Idempotency-Key"] = key
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs",
            headers=headers,
            json={"session_id": session_id, "question": question, "idempotency_key": key},
            timeout=180,
        )
        response.raise_for_status()
        return response.json()

    def stream_start_run(
        self, session_id: str, question: str, idempotency_key: str | None = None
    ) -> Iterator[dict[str, Any]]:
        key = idempotency_key or str(uuid4())
        yield from self._stream(
            "/api/v1/agent/runs/stream",
            {"session_id": session_id, "question": question, "idempotency_key": key},
            idempotency_key=key,
        )

    def feedback(
        self,
        run_id: str,
        decision: str,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        key = idempotency_key or str(uuid4())
        headers = self._headers()
        headers["Idempotency-Key"] = key
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/feedback",
            headers=headers,
            json={
                "decision": decision,
                "comment": comment or None,
                "idempotency_key": key,
            },
            timeout=180,
        )
        response.raise_for_status()
        return response.json()

    def stream_feedback(
        self,
        run_id: str,
        decision: str,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        key = idempotency_key or str(uuid4())
        yield from self._stream(
            f"/api/v1/agent/runs/{run_id}/feedback/stream",
            {
                "decision": decision,
                "comment": comment or None,
                "idempotency_key": key,
            },
            idempotency_key=key,
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/cancel",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return response.json()


    def download_excel(self, run_id: str) -> bytes:
        """Generate and return the governed XLSX for a completed run.

        This method is intentionally compatible with Streamlit's deferred download callable:
        the HTTP request is only executed when the user clicks the download button.
        """
        response = httpx.get(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/exports/excel",
            headers=self._headers(),
            timeout=60,
        )
        response.raise_for_status()
        return response.content

    def export_excel(self, run_id: str) -> tuple[bytes, str]:
        """Backward-compatible helper for clients that also need a filename."""
        response = httpx.get(
            f"{self.base_url}/api/v1/agent/runs/{run_id}/exports/excel",
            headers=self._headers(),
            timeout=60,
        )
        response.raise_for_status()
        disposition = response.headers.get("content-disposition", "")
        filename = f"resultado-sql-{run_id[:8]}.xlsx"
        marker = 'filename="'
        if marker in disposition:
            filename = disposition.split(marker, 1)[1].split('"', 1)[0]
        return response.content, filename

    def _stream(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        timeout = httpx.Timeout(connect=30, read=None, write=30, pool=30)
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
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
