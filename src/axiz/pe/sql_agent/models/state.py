from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dict_lists(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(left or []) + list(right or [])


class AgentState(TypedDict, total=False):
    run_id: str
    session_id: str
    user_id: str
    _lease_owner: str
    question: str
    resolved_question: str
    context_resolution: dict[str, Any]
    follow_up_change_plan: bool
    conversation_history: list[dict[str, str]]
    conversation_memory: dict[str, Any]

    autonomous_available: bool
    autonomous_enabled: bool
    autonomous_mode: str
    autonomous_routing_decision: dict[str, Any]
    autonomous_plan: dict[str, Any]
    autonomous_current_task_id: str | None
    autonomous_specialist_output: dict[str, Any]
    autonomous_query_mode: str
    autonomous_evidence: list[dict[str, Any]]
    autonomous_critic_review: dict[str, Any]
    autonomous_supervisor_decision: dict[str, Any]
    autonomous_budget: dict[str, Any]
    autonomous_budget_usage: dict[str, Any]
    autonomous_iteration: int
    autonomous_queries_executed: int
    autonomous_rejected_conclusions: list[str]
    autonomous_primary_evidence_id: str | None
    autonomous_grounded_findings: list[dict[str, Any]]
    autonomous_published_domains: list[dict[str, Any]]
    autonomous_previous_sql: str
    autonomous_dispatch_task: dict[str, Any]
    autonomous_dispatch_task_ids: list[str]
    autonomous_wave: int
    autonomous_pending_proposals: list[dict[str, Any]]
    autonomous_proposals: list[dict[str, Any]]
    autonomous_proposal_updates: Annotated[list[dict[str, Any]], merge_dict_lists]
    autonomous_trajectory: list[dict[str, Any]]
    autonomous_trajectory_updates: Annotated[list[dict[str, Any]], merge_dict_lists]
    autonomous_trajectory_sequence: int
    autonomous_current_proposal_id: str | None

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
    query_spec: dict[str, Any]
    compiled_sql_artifact: dict[str, Any]
    sql_execution_state: str
    review_revision: int

    approval_status: str
    feedback_comment: str | None
    previous_review_sql: str
    feedback_plan: dict[str, Any]
    feedback_application: dict[str, Any]
    feedback_compliance: dict[str, Any]
    feedback_repair_attempts: int
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
