from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.models.state import AgentState
from axiz.pe.sql_agent.workflow.nodes import (
    WorkflowNodes,
    route_after_classification,
    route_after_context_resolution,
    route_after_cost,
    route_after_exploration,
    route_after_feedback_compliance,
    route_after_feedback_interpretation,
    route_after_review,
    route_after_security,
)


def build_graph(nodes: WorkflowNodes) -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("resolve_context", nodes.resolve_context)
    graph.add_node("classify", nodes.classify)
    graph.add_node("answer_capabilities", nodes.answer_capabilities)
    graph.add_node("answer_conversation_context", nodes.answer_conversation_context)
    graph.add_node("explore_semantics", nodes.explore_semantics)
    graph.add_node("answer_catalog", nodes.answer_catalog)
    graph.add_node("interpret_feedback", nodes.interpret_feedback)
    graph.add_node("generate_sql", nodes.generate_sql)
    graph.add_node("apply_feedback", nodes.apply_feedback)
    graph.add_node("validate_feedback_compliance", nodes.validate_feedback_compliance)
    graph.add_node("validate_security", nodes.validate_security)
    graph.add_node("estimate_cost", nodes.estimate_cost)
    graph.add_node("estimate_llm_approval", nodes.estimate_llm_approval)
    graph.add_node("human_review", nodes.human_review)
    graph.add_node("execute_sql", nodes.execute_sql)
    graph.add_node("verify_result", nodes.verify_result)
    graph.add_node("explain", nodes.explain)
    graph.add_node("unsupported", nodes.unsupported)
    graph.add_node("clarification", nodes.clarification)
    graph.add_node("rejected", nodes.rejected)

    graph.add_edge(START, "resolve_context")
    graph.add_conditional_edges(
        "resolve_context",
        route_after_context_resolution,
        {"classify": "classify", "clarification": "clarification"},
    )
    graph.add_conditional_edges(
        "classify",
        route_after_classification,
        {
            "answer_capabilities": "answer_capabilities",
            "answer_conversation_context": "answer_conversation_context",
            "unsupported": "unsupported",
            "clarification": "clarification",
            "explore_semantics": "explore_semantics",
        },
    )
    graph.add_conditional_edges(
        "explore_semantics",
        route_after_exploration,
        {
            "answer_catalog": "answer_catalog",
            "generate_sql": "generate_sql",
        },
    )
    graph.add_edge("generate_sql", "apply_feedback")
    graph.add_edge("apply_feedback", "validate_feedback_compliance")
    graph.add_conditional_edges(
        "validate_feedback_compliance",
        route_after_feedback_compliance,
        {
            "validate_security": "validate_security",
            "generate_sql": "generate_sql",
            "clarification": "clarification",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "validate_security",
        route_after_security,
        {
            "estimate_cost": "estimate_cost",
            "generate_sql": "generate_sql",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "estimate_cost",
        route_after_cost,
        {"estimate_llm_approval": "estimate_llm_approval", "end": END},
    )
    graph.add_edge("estimate_llm_approval", "human_review")
    graph.add_conditional_edges(
        "human_review",
        route_after_review,
        {
            "execute_sql": "execute_sql",
            "interpret_feedback": "interpret_feedback",
            "rejected": "rejected",
        },
    )
    graph.add_conditional_edges(
        "interpret_feedback",
        route_after_feedback_interpretation,
        {
            "generate_sql": "generate_sql",
            "apply_feedback": "apply_feedback",
            "clarification": "clarification",
        },
    )
    graph.add_edge("execute_sql", "verify_result")
    graph.add_edge("verify_result", "explain")

    for terminal in (
        "answer_capabilities",
        "answer_conversation_context",
        "answer_catalog",
        "explain",
        "unsupported",
        "clarification",
        "rejected",
    ):
        graph.add_edge(terminal, END)
    return graph
