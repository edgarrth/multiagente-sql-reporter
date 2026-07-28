from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from axiz.pe.sql_agent.skills.coordinator.context_resolution import ContextResolutionSkill
from axiz.pe.sql_agent.models.contracts import (
    ContextRelation,
    ContextResolutionOutput,
    ConversationMemory,
    CostValidation,
    LLMCallUsage,
    LLMUsageSummary,
    QueryResult,
    RunResponse,
    RunStatus,
    SecurityValidation,
)
from axiz.pe.sql_agent.services.conversation_memory import (
    StructuredConversationMemoryService,
)


class FailingLLM:
    async def parse(self, **kwargs):
        raise RuntimeError("provider unavailable")


class StaticResolverLLM:
    def __init__(self, output: ContextResolutionOutput) -> None:
        self.output = output

    async def parse(self, **kwargs):
        return self.output


@pytest.mark.asyncio
async def test_semantically_complete_request_is_independent_without_keyword_rules() -> None:
    agent = ContextResolutionSkill(
        StaticResolverLLM(
            ContextResolutionOutput(
                original_question="ignored",
                resolved_question="ignored",
                relation=ContextRelation.INDEPENDENT_REQUEST,
                confidence=0.98,
                rationale="The request states a complete analytical objective and scope.",
            )
        )
    )  # type: ignore[arg-type]
    question = "¿Qué entidades tuvieron el mayor valor durante el periodo anterior?"
    output = await agent.resolve(
        question=question,
        memory=ConversationMemory(),
        history=[],
    )
    assert output.relation == ContextRelation.INDEPENDENT_REQUEST
    assert output.resolved_question == question
    assert output.is_follow_up is False
    assert output.requires_clarification is False


@pytest.mark.asyncio
async def test_first_turn_without_state_routes_as_new_request() -> None:
    agent = ContextResolutionSkill(
        StaticResolverLLM(
            ContextResolutionOutput(
                original_question="ignored",
                resolved_question="ignored",
                relation=ContextRelation.ANALYTICAL_FOLLOW_UP,
                confidence=0.99,
                rationale="This output must be bypassed because no prior state exists.",
            )
        )
    )  # type: ignore[arg-type]
    output = await agent.resolve(
        question="Aumenta el límite",
        memory=ConversationMemory(),
        history=[],
    )
    # Dependency resolution must not invent a previous query. The catalog/SQL stage can still ask
    # what should be limited, but this layer treats the first turn as a fresh request.
    assert output.relation == ContextRelation.INDEPENDENT_REQUEST
    assert output.is_follow_up is False
    assert output.requires_clarification is False


@pytest.mark.asyncio
async def test_analytical_follow_up_is_rewritten_from_structured_memory() -> None:
    agent = ContextResolutionSkill(
        StaticResolverLLM(
            ContextResolutionOutput(
                original_question="ignored",
                resolved_question=(
                    "Obtener el monto por la dimensión previamente aprobada, conservando todos "
                    "los criterios y agregando el nuevo filtro solicitado."
                ),
                relation=ContextRelation.ANALYTICAL_FOLLOW_UP,
                inherited_fields=["metrics", "dimensions", "time_window", "limit"],
                confidence=0.96,
                rationale="The instruction modifies the prior analytical request.",
            )
        )
    )  # type: ignore[arg-type]
    output = await agent.resolve(
        question="Aplica además el nuevo filtro",
        memory=ConversationMemory(
            last_resolved_question="Obtener una métrica por una dimensión.",
            last_domain="acquiring",
            last_metrics=["processed_amount_pen"],
            last_dimensions=["merchant_name"],
            last_sql="SELECT merchant_name FROM semantic.v_merchant_performance LIMIT 300",
            last_limit=300,
        ),
        history=[],
    )
    assert output.relation == ContextRelation.ANALYTICAL_FOLLOW_UP
    assert output.is_follow_up is True
    assert output.requires_sql_revision is True
    assert "limit" in output.inherited_fields


@pytest.mark.asyncio
async def test_session_reference_does_not_request_new_sql() -> None:
    question = "Resume lo que se ejecutó anteriormente"
    agent = ContextResolutionSkill(
        StaticResolverLLM(
            ContextResolutionOutput(
                original_question="ignored",
                resolved_question="ignored",
                relation=ContextRelation.SESSION_REFERENCE,
                confidence=0.95,
                rationale="The user asks about prior session state.",
            )
        )
    )  # type: ignore[arg-type]
    output = await agent.resolve(
        question=question,
        memory=ConversationMemory(last_sql="SELECT 1"),
        history=[],
    )
    assert output.relation == ContextRelation.SESSION_REFERENCE
    assert output.resolved_question == question
    assert output.requires_sql_revision is False


@pytest.mark.asyncio
async def test_context_provider_failure_without_memory_falls_back_to_independent_router() -> None:
    agent = ContextResolutionSkill(FailingLLM())  # type: ignore[arg-type]
    output = await agent.resolve(
        question="Nueva solicitud completa",
        memory=ConversationMemory(),
        history=[],
    )
    assert output.relation == ContextRelation.INDEPENDENT_REQUEST
    assert output.requires_clarification is False


def test_completed_analytical_run_builds_bounded_structured_memory() -> None:
    service = StructuredConversationMemoryService()
    run_id = uuid4()
    session_id = uuid4()
    response = RunResponse(
        run_id=run_id,
        session_id=session_id,
        status=RunStatus.COMPLETED,
        answer="Lima concentró el mayor monto.",
        key_findings=["Lima lidera"],
        security_validation=SecurityValidation(approved=True),
        cost_validation=CostValidation(approved=True),
        result=QueryResult(
            columns=["city", "processed_amount_pen"],
            rows=[
                {"city": f"city-{index}", "processed_amount_pen": index}
                for index in range(10)
            ],
            row_count=10,
            elapsed_ms=12.5,
        ),
        llm_usage=LLMUsageSummary(
            call_count=1,
            completed_calls=1,
            actual_input_tokens=100,
            actual_output_tokens=20,
            actual_total_tokens=120,
            calls=[
                LLMCallUsage(
                    call_id="call-1",
                    agent="explanation",
                    provider="openai",
                    model="model-a",
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                )
            ],
        ),
    )
    state = {
        "intent": "analytical_query",
        "question": "¿Cuál fue el monto por ciudad?",
        "resolved_question": "Obtener el monto procesado por ciudad del último mes.",
        "interpretation": "Monto por ciudad",
        "domain": "acquiring",
        "selected_metrics": ["processed_amount_pen"],
        "selected_dimensions": ["city"],
        "selected_filters": [
            {"field": "city", "operator": "=", "value": "Lima", "source": "user"}
        ],
        "time_window": {
            "label": "último mes cerrado",
            "start_expression": "DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month'",
            "end_expression": "DATE_TRUNC('month', CURRENT_DATE)",
            "grain": "month",
            "closed_period": True,
        },
        "generated_sql": "SELECT city FROM semantic.v_daily_payment_metrics",
    }
    memory = service.merge(ConversationMemory(), state, response)
    assert memory.last_run_id == run_id
    assert memory.last_metrics == ["processed_amount_pen"]
    assert memory.last_dimensions == ["city"]
    assert memory.last_filters[0].value == "Lima"
    assert memory.last_time_window and memory.last_time_window.closed_period is True
    assert memory.last_result_schema == ["city", "processed_amount_pen"]
    assert len(memory.last_result_sample) == 5
    assert memory.last_row_count == 10
    assert memory.last_models == ["model-a"]
    assert memory.last_token_usage == 120


def test_control_schema_contains_versioned_session_memory_table() -> None:
    sql = Path("infrastructure/postgres/init/01-app-tables.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE TABLE IF NOT EXISTS app.session_memory" in sql
    assert "memory jsonb" in sql
    assert "revision integer" in sql
    assert "ON DELETE CASCADE" in sql


def test_graph_resolves_context_before_classification() -> None:
    source = Path("src/axiz/pe/sql_agent/workflow/graph.py").read_text(encoding="utf-8")
    assert 'graph.add_edge(START, "resolve_context")' in source
    assert 'graph.add_node("resolve_context", nodes.resolve_context)' in source


def test_sql_memory_extractor_adds_actual_where_filters() -> None:
    pytest.importorskip("sqlglot")
    service = StructuredConversationMemoryService(sql_dialect="postgres")
    response = RunResponse(
        run_id=uuid4(),
        session_id=uuid4(),
        status=RunStatus.AWAITING_APPROVAL,
    )
    state = {
        "intent": "analytical_query",
        "question": "Dame monto de Lima",
        "resolved_question": "Obtener monto procesado para Lima en el último mes.",
        "interpretation": "Monto procesado para Lima",
        "domain": "acquiring",
        "generated_sql": (
            "SELECT city, processed_amount_pen "
            "FROM semantic.v_daily_payment_metrics "
            "WHERE city = 'Lima' "
            "AND metric_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month' "
            "AND metric_date < DATE_TRUNC('month', CURRENT_DATE) LIMIT 500"
        ),
    }
    memory = service.merge(ConversationMemory(), state, response)
    assert any(item.field == "city" and "Lima" in item.value for item in memory.last_filters)
    assert memory.last_time_window is not None
    assert memory.last_time_window.start_expression is not None
    assert memory.last_time_window.end_expression is not None
