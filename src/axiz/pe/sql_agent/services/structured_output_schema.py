from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def incompatible_open_object_paths(response_model: type[BaseModel]) -> list[str]:
    """Return JSON-Schema paths that expose arbitrary object properties.

    Strict Structured Outputs require object shapes to be closed. Pydantic fields such
    as ``dict[str, Any]`` emit ``additionalProperties`` and therefore must not be part
    of an LLM response contract. Runtime metadata belongs in deterministic artifacts.
    """

    schema = response_model.model_json_schema()
    issues: list[str] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" in node:
                if node.get("additionalProperties") is not False:
                    issues.append(path)
            for key, value in node.items():
                visit(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                visit(value, f"{path}[{index}]")

    visit(schema, "$")
    return issues


def ensure_closed_response_schema(response_model: type[BaseModel]) -> None:
    issues = incompatible_open_object_paths(response_model)
    if not issues:
        return
    joined = ", ".join(issues)
    raise ValueError(
        f"Response model {response_model.__name__!r} contains open JSON objects at "
        f"{joined}. Replace dict/Any response fields with closed Pydantic contracts or "
        "move the metadata to a deterministic post-processing artifact."
    )
