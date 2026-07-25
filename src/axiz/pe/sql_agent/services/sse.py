from __future__ import annotations

import json


def encode_sse(event: dict) -> str:
    event_type = str(event.get("type") or "message")
    data = json.dumps(event.get("data") or {}, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"
