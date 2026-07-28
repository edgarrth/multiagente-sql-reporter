from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.models.state import AgentState
from axiz.pe.sql_agent.workflow.nodes import (
    WorkflowNodes,
    route_after_autonomous_rejection,
    route_after_classification,
    route_after_context_resolution,
    route_after_cost,
    route_after_evidence_recorded,
    route_after_exploration,
    route_after_feedback_compliance,
    route_after_feedback_interpretation,
    route_after_review,
    route_after_proposal_selection,
    route_after_security,
    route_after_specialist_collection,
    route_after_verification,
)


def build_graph(nodes: WorkflowNodes) -> StateGraph:
    """Build the coordinator-led governed autonomous society.

    Four reasoning roles are active: Investigation Coordinator, configurable Domain Analyst,
    SQL Engineer and Evidence Reviewer. Specialist profiles are compiled dynamically from
    ``config/specialists.yaml``. Security, cost, budgets, HITL and execution stay deterministic.
    """

    graph = StateGraph(AgentState)
    graph.add_node("resolve_context", nodes.resolve_context)
    graph.add_node("initialize_society", nodes.initialize_society)
    graph.add_node("select_investigation_mode", nodes.select_investigation_mode)
    graph.add_node("direct_failure", nodes.direct_failure)
    graph.add_node("synthesize_direct_investigation", nodes.synthesize_direct_investigation)
    graph.add_node("plan_investigation", nodes.plan_investigation)
    graph.add_node("supervisor_review", nodes.supervisor_review)
    graph.add_node("collect_specialist_wave", nodes.collect_specialist_wave)
    graph.add_node("select_next_proposal", nodes.select_next_proposal)
    graph.add_node("reject_autonomous_proposal", nodes.reject_autonomous_proposal)
    graph.add_node("record_evidence", nodes.record_evidence)
    graph.add_node("critic_review", nodes.critic_review)
    graph.add_node("synthesize_investigation", nodes.synthesize_investigation)

    # Dynamic specialist subgraphs. Adding a specialist requires config + semantic contracts,
    # not a hard-coded branch in this graph.
    specialist_nodes = nodes.specialist_graph_registry.node_functions()
    for node_name, node_function in specialist_nodes.items():
        graph.add_node(node_name, node_function)

    # Existing governed capabilities remain available for context, feedback repairs and direct
    # non-analytical requests.
    graph.add_node("classify", nodes.classify)
    graph.add_node("answer_capabilities", nodes.answer_capabilities)
    graph.add_node("answer_conversation_context", nodes.answer_conversation_context)
    graph.add_node("explore_semantics", nodes.explore_semantics)
    graph.add_node("answer_catalog", nodes.answer_catalog)
    graph.add_node("interpret_follow_up", nodes.interpret_follow_up)
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
        {
            "classify": "classify",
            "answer_conversation_context": "answer_conversation_context",
            "explore_semantics": "explore_semantics",
            "initialize_society": "initialize_society",
            "clarification": "clarification",
        },
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
            "initialize_society": "initialize_society",
        },
    )

    graph.add_edge("initialize_society", "select_investigation_mode")
    graph.add_conditional_edges(
        "select_investigation_mode",
        nodes.route_investigation_mode,
        {
            "plan_investigation": "plan_investigation",
            "clarification": "clarification",
            "direct_failure": "direct_failure",
            "end": END,
        },
    )
    graph.add_edge("plan_investigation", "supervisor_review")

    def supervisor_dispatch(state: AgentState):
        result = nodes.route_supervisor_dispatch(state)
        return END if result == "end" else result

    # This conditional route can return one node name or a bounded list of Send objects.
    graph.add_conditional_edges("supervisor_review", supervisor_dispatch)
    for specialist_node in specialist_nodes:
        graph.add_edge(specialist_node, "collect_specialist_wave")
    graph.add_conditional_edges(
        "collect_specialist_wave",
        route_after_specialist_collection,
        {
            "select_next_proposal": "select_next_proposal",
            "critic_review": "critic_review",
            "direct_failure": "direct_failure",
        },
    )
    graph.add_conditional_edges(
        "select_next_proposal",
        route_after_proposal_selection,
        {
            "estimate_llm_approval": "estimate_llm_approval",
            "critic_review": "critic_review",
            "direct_failure": "direct_failure",
        },
    )

    graph.add_conditional_edges(
        "explore_semantics",
        route_after_exploration,
        {
            "answer_catalog": "answer_catalog",
            "interpret_follow_up": "interpret_follow_up",
            "generate_sql": "generate_sql",
        },
    )
    graph.add_conditional_edges(
        "interpret_follow_up",
        route_after_feedback_interpretation,
        {
            "generate_sql": "generate_sql",
            "apply_feedback": "apply_feedback",
            "clarification": "clarification",
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
            "reject_autonomous_proposal": "reject_autonomous_proposal",
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
    graph.add_conditional_edges(
        "verify_result",
        route_after_verification,
        {"record_evidence": "record_evidence", "explain": "explain"},
    )
    graph.add_conditional_edges(
        "record_evidence",
        route_after_evidence_recorded,
        {
            "select_next_proposal": "select_next_proposal",
            "critic_review": "critic_review",
            "synthesize_direct_investigation": "synthesize_direct_investigation",
        },
    )
    graph.add_conditional_edges(
        "reject_autonomous_proposal",
        route_after_autonomous_rejection,
        {
            "select_next_proposal": "select_next_proposal",
            "critic_review": "critic_review",
            "rejected": "rejected",
        },
    )
    graph.add_edge("critic_review", "supervisor_review")

    for terminal in (
        "answer_capabilities",
        "answer_conversation_context",
        "answer_catalog",
        "explain",
        "unsupported",
        "clarification",
        "rejected",
        "synthesize_investigation",
        "synthesize_direct_investigation",
        "direct_failure",
    ):
        graph.add_edge(terminal, END)
    return graph
