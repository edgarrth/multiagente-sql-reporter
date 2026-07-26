from __future__ import annotations

import ast
from pathlib import Path


def _keywords_for_call(tree: ast.AST, call_name: str) -> set[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == call_name:
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    raise AssertionError(f"No se encontró la llamada a {call_name}")


def test_excel_export_tool_is_wired_to_workflow_service_not_nodes() -> None:
    source = Path("src/axiz/pe/sql_agent/container.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    node_keywords = _keywords_for_call(tree, "WorkflowNodes")
    service_keywords = _keywords_for_call(tree, "AgentWorkflowService")

    assert "excel_exports" not in node_keywords
    assert "excel_exports" in service_keywords
    assert "llm_approval_estimator" in node_keywords
    assert "context_resolver_agent" in node_keywords
    assert "memories" in service_keywords
    assert "memory_service" in service_keywords
    assert "execution_coordinator" in service_keywords
    assert "query_engine" in node_keywords
    assert "query_tool" not in node_keywords
