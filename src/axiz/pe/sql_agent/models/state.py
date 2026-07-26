from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    user_id: str
    _lease_owner: str
    question: str
    resolved_question: str
    context_resolution: dict[str, Any]
    conversation_history: list[dict[str, str]]
    conversation_memory: dict[str, Any]

    intent: str
    domain: str | None
    domain_confidence: float
    clarification_question: str | None

    semantic_context: dict[str, Any]
    selected_examples: list[dict[str, Any]]

    generated_sql: str
    interpretation: str
    assumptions: list[str]
    selected_metrics: list[str]
    selected_dimensions: list[str]
    selected_filters: list[dict[str, Any]]
    time_window: dict[str, Any] | None
    source_objects: list[str]
    review_revision: int

    approval_status: str
    feedback_comment: str | None
    repair_attempts: int

    security_validation: dict[str, Any]
    cost_validation: dict[str, Any]
    llm_usage: dict[str, Any]
    llm_approval_estimate: dict[str, Any]

    query_result: dict[str, Any]
    verification: dict[str, Any]
    answer: str
    key_findings: list[str]
    caveats: list[str]
    visualization: dict[str, Any]

    status: str
    error: str | None
