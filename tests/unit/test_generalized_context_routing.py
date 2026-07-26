from axiz.pe.sql_agent.models.contracts import ContextRelation
from axiz.pe.sql_agent.workflow.context_routing import (
    route_after_context_resolution,
    route_after_exploration,
)


def test_analytical_follow_up_bypasses_generic_intent_router() -> None:
    state = {
        "context_resolution": {
            "relation": ContextRelation.ANALYTICAL_FOLLOW_UP.value,
            "requires_clarification": False,
        },
        "conversation_memory": {
            "last_sql": "SELECT 1",
            "last_domain": "acquiring",
        },
        "domain": "acquiring",
    }
    assert route_after_context_resolution(state) == "explore_semantics"


def test_independent_request_uses_generic_intent_router() -> None:
    state = {
        "context_resolution": {
            "relation": ContextRelation.INDEPENDENT_REQUEST.value,
            "requires_clarification": False,
        },
        "conversation_memory": {"last_sql": "SELECT 1"},
    }
    assert route_after_context_resolution(state) == "classify"


def test_follow_up_after_semantic_exploration_always_enters_sql_revision_pipeline() -> None:
    state = {
        "intent": "analytical_query",
        "context_resolution": {
            "relation": ContextRelation.ANALYTICAL_FOLLOW_UP.value,
        },
        "conversation_memory": {"last_sql": "SELECT 1"},
    }
    assert route_after_exploration(state) == "interpret_follow_up"


def test_context_resolver_has_no_domain_specific_or_phrase_regex_router() -> None:
    from pathlib import Path

    source = Path(
        "src/axiz/pe/sql_agent/agents/context_resolver_agent.py"
    ).read_text(encoding="utf-8")
    assert "_FOLLOW_UP_PATTERNS" not in source
    assert "re.compile" not in source
    assert "Decide from the complete semantic meaning" in source


def test_session_reference_bypasses_intent_router_and_never_generates_sql() -> None:
    state = {
        "context_resolution": {
            "relation": ContextRelation.SESSION_REFERENCE.value,
            "requires_clarification": False,
        },
        "conversation_memory": {"last_sql": "SELECT 1"},
        "intent": "conversation_question",
    }
    assert route_after_context_resolution(state) == "answer_conversation_context"


def test_workflow_nodes_imports_context_relation_used_at_runtime() -> None:
    """Prevents a startup-clean but first-request NameError in resolve_context."""
    import ast
    from pathlib import Path

    source_path = Path("src/axiz/pe/sql_agent/workflow/nodes.py")
    module = ast.parse(source_path.read_text(encoding="utf-8"))

    imported_names = {
        alias.asname or alias.name
        for node in module.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "axiz.pe.sql_agent.models.contracts"
        for alias in node.names
    }

    assert "ContextRelation" in imported_names
