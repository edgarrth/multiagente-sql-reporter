from __future__ import annotations

from axiz.pe.sql_agent.models.contracts import ContextRelation, InvestigationQueryMode
from axiz.pe.sql_agent.models.state import AgentState


def route_after_context_resolution(state: AgentState) -> str:
    """Route based on semantic dependency, never on domain-specific words."""
    resolution = state.get("context_resolution", {})
    if resolution.get("requires_clarification"):
        return "clarification"
    if resolution.get("relation") == ContextRelation.SESSION_REFERENCE.value:
        return "answer_conversation_context"
    if (
        resolution.get("relation") == ContextRelation.ANALYTICAL_FOLLOW_UP.value
        and (state.get("conversation_memory") or {}).get("last_sql")
        and state.get("domain")
    ):
        return (
            "initialize_society"
            if state.get("autonomous_available", state.get("autonomous_enabled"))
            else "explore_semantics"
        )
    return "classify"


def route_after_exploration(state: AgentState) -> str:
    if state.get("intent") == "catalog_question":
        return "answer_catalog"
    resolution = state.get("context_resolution") or {}
    memory = state.get("conversation_memory") or {}
    is_follow_up = (
        resolution.get("relation") == ContextRelation.ANALYTICAL_FOLLOW_UP.value
        and memory.get("last_sql")
    )
    if is_follow_up and (
        not state.get("autonomous_enabled")
        or state.get("autonomous_query_mode")
        == InvestigationQueryMode.REVISE_PREVIOUS.value
    ):
        return "interpret_follow_up"
    return "generate_sql"
