from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.models.state import AgentState
from axiz.pe.sql_agent.workflow.nodes import (
    WorkflowNodes,
    route_after_classification,
    route_after_cost,
    route_after_exploration,
    route_after_review,
    route_after_security,
)


def build_graph(nodes: WorkflowNodes) -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("classify", nodes.classify)
    graph.add_node("explore_semantics", nodes.explore_semantics)
    graph.add_node("answer_catalog", nodes.answer_catalog)
    graph.add_node("generate_sql", nodes.generate_sql)
    graph.add_node("human_review", nodes.human_review)
    graph.add_node("validate_security", nodes.validate_security)
    graph.add_node("estimate_cost", nodes.estimate_cost)
    graph.add_node("execute_sql", nodes.execute_sql)
    graph.add_node("verify_result", nodes.verify_result)
    graph.add_node("explain", nodes.explain)
    graph.add_node("unsupported", nodes.unsupported)
    graph.add_node("clarification", nodes.clarification)
    graph.add_node("rejected", nodes.rejected)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_after_classification,
        {
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
    graph.add_edge("generate_sql", "human_review")
    graph.add_conditional_edges(
        "human_review",
        route_after_review,
        {
            "validate_security": "validate_security",
            "generate_sql": "generate_sql",
            "rejected": "rejected",
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
        {"execute_sql": "execute_sql", "end": END},
    )
    graph.add_edge("execute_sql", "verify_result")
    graph.add_edge("verify_result", "explain")

    for terminal in ("answer_catalog", "explain", "unsupported", "clarification", "rejected"):
        graph.add_edge(terminal, END)
    return graph
