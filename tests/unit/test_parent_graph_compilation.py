from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GRAPH_SOURCE = ROOT / "src/axiz/pe/sql_agent/workflow/graph.py"


def test_add_conditional_edges_never_exceeds_langgraph_positional_contract() -> None:
    """Protect the parent graph from malformed LangGraph API calls.

    StateGraph.add_conditional_edges accepts source, path and an optional path_map.
    A fourth positional argument prevents FastAPI from starting.
    """

    tree = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_conditional_edges"
    ]
    assert calls, "The parent graph should define conditional routing"
    invalid = [(node.lineno, len(node.args)) for node in calls if len(node.args) > 3]
    assert invalid == []


def test_parent_graph_compiles_with_installed_langgraph() -> None:
    """Compile the real topology using lightweight node implementations.

    This test runs in the project/Docker environment where LangGraph and all runtime
    dependencies are installed. It validates the actual StateGraph API and topology, not
    only source text.
    """

    pytest.importorskip("langgraph")
    from axiz.pe.sql_agent.workflow.graph import build_graph

    def noop(state):
        return {}

    class SpecialistGraphRegistry:
        @staticmethod
        def node_functions():
            return {"specialist__compilation_smoke": noop}

    class Nodes:
        specialist_graph_registry = SpecialistGraphRegistry()

        @staticmethod
        def route_supervisor_dispatch(state):
            return "end"

        def __getattr__(self, name):
            return noop

    compiled = build_graph(Nodes()).compile()
    assert compiled is not None
