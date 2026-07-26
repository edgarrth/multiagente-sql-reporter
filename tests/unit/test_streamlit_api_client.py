from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "streamlit_app"))

from api_client import ApiClient  # noqa: E402


def test_download_excel_returns_bytes_for_deferred_download(monkeypatch) -> None:
    content = b"PK\x03\x04fake-xlsx"

    def fake_get(*args, **kwargs):
        request = httpx.Request("GET", "http://api.test/export.xlsx")
        return httpx.Response(200, content=content, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    client = ApiClient(token="test-token")
    assert client.download_excel("12345678-1234-1234-1234-123456789012") == content


def test_start_run_sends_idempotency_key(monkeypatch) -> None:
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        request = httpx.Request("POST", "http://api.test/runs")
        return httpx.Response(202, json={"run_id": "r"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    client = ApiClient(token="test-token")
    client.start_run("session", "question", idempotency_key="12345678-fixed")

    assert captured["headers"]["Idempotency-Key"] == "12345678-fixed"
    assert captured["json"]["idempotency_key"] == "12345678-fixed"
