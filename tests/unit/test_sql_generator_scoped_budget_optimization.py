from __future__ import annotations

import json

import pytest

from axiz.pe.sql_agent.skills.sql.generation import SqlGenerationSkill
from axiz.pe.sql_agent.models.contracts import SqlGenerationOutput
from axiz.pe.sql_agent.services.llm import PromptBudget
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector


class CapturingLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def parse(self, *, system: str, user: str, response_model):
        self.calls.append({"system": system, "user": user})
        assert response_model is SqlGenerationOutput
        return SqlGenerationOutput(
            sql=(
                "SELECT city, SUM(approved_count) AS approved_count "
                "FROM semantic.v_daily_payment_metrics "
                "WHERE metric_date >= CURRENT_DATE - INTERVAL '1 month' "
                "GROUP BY city ORDER BY approved_count DESC LIMIT 100"
            ),
            interpretation="Tasa de aprobación por ciudad",
            selected_metrics=["approved_count"],
            selected_dimensions=["city"],
            source_objects=["semantic.v_daily_payment_metrics"],
        )


@pytest.mark.asyncio
async def test_validator_retry_uses_dedicated_compact_sql_repair_prompt() -> None:
    primary = CapturingLLM()
    repair = CapturingLLM()
    agent = SqlGenerationSkill(
        primary,
        dialect="postgres",
        max_result_rows=500,
        repair_llm=repair,
    )
    semantic_context = {
        "allowed_sources": [
            "semantic.v_daily_payment_metrics",
            "semantic.v_payment_transactions",
            "semantic.v_chargeback_metrics",
        ],
        "query_policy": {"read_only": True},
        "calendar_context": {"timezone": "America/Lima"},
        "source_contracts": {
            "semantic.v_daily_payment_metrics": {
                "columns": ["metric_date", "city", "approved_count", "transaction_count"],
            },
            "semantic.v_payment_transactions": {
                "columns": [f"transaction_column_{index}" for index in range(120)],
            },
            "semantic.v_chargeback_metrics": {
                "columns": [f"chargeback_column_{index}" for index in range(120)],
            },
        },
        "semantic_symbols": {
            "metrics": [
                {"name": "approved_count", "column": "approved_count"},
                {"name": "transaction_count", "column": "transaction_count"},
            ],
            "dimensions": [{"name": "city", "column": "city"}],
            "sources": [
                {"name": "daily", "source": "semantic.v_daily_payment_metrics"},
                {"name": "transactions", "source": "semantic.v_payment_transactions"},
            ],
        },
        "catalog_hits": [{"content": "intentionally large optional document" * 500}],
        "selected_examples": [{"sql": "SELECT ..." * 500}],
    }
    failed_sql = (
        "SELECT city, SUM(approved_count) FROM semantic.v_daily_payment_metrics "
        "GROUP BY invalid_city LIMIT 100"
    )

    await agent.generate(
        question="Tasa de aprobación por ciudad",
        semantic_context=semantic_context,
        history=[{"role": "assistant", "content": "old result" * 1000}],
        structured_memory={"last_sql": failed_sql, "last_interpretation": "old"},
        prior_compliance={
            "retry_instruction": "column invalid_city does not exist",
            "failed_sql": failed_sql,
        },
        current_contract={
            "selected_metrics": ["approved_count", "transaction_count"],
            "selected_dimensions": ["city"],
            "source_objects": ["semantic.v_daily_payment_metrics"],
        },
    )

    assert primary.calls == []
    assert len(repair.calls) == 1
    payload = json.loads(repair.calls[0]["user"])
    assert list(payload["repair_context"]["source_contracts"]) == [
        "semantic.v_daily_payment_metrics"
    ]
    assert "recent_conversation" not in payload
    assert "structured_memory" not in payload
    assert "catalog_hits" not in payload["repair_context"]
    estimated = PromptBudget.estimate_tokens(repair.calls[0]["system"]) + PromptBudget.estimate_tokens(
        repair.calls[0]["user"]
    )
    # Leaves room for a 1,400-token response even after 18K tokens were already consumed.
    assert estimated + 1400 < 6000


def test_semantic_projection_limits_candidate_contracts_but_keeps_security_allowlist() -> None:
    projector = SemanticContextProjector(max_source_contracts=2)
    full_context = {
        "allowed_sources": ["semantic.a", "semantic.b", "semantic.c"],
        "query_policy": {"read_only": True},
        "source_contracts": {
            "semantic.a": {"name": "approval", "columns": ["city", "approved_count"]},
            "semantic.b": {"name": "declines", "columns": ["response_code"]},
            "semantic.c": {"name": "chargebacks", "columns": ["reason_code"]},
        },
        "semantic_symbols": {
            "metrics": [{"name": "approved_count", "column": "approved_count"}],
            "dimensions": [{"name": "city", "column": "city"}],
            "sources": [
                {"name": "approval", "source": "semantic.a"},
                {"name": "declines", "source": "semantic.b"},
                {"name": "chargebacks", "source": "semantic.c"},
            ],
        },
        "catalog_hits": [
            {
                "score": 10,
                "kind": "entity",
                "path": "approval.yaml",
                "content": {"name": "approval", "source": "semantic.a"},
            }
        ],
        "selected_examples": [
            {"question": "approval by city", "sql": "SELECT city FROM semantic.a"}
        ],
    }

    projected = projector.project(
        question="approval by city",
        full_context=full_context,
    )

    assert projected["allowed_sources"] == ["semantic.a", "semantic.b", "semantic.c"]
    assert len(projected["source_contracts"]) == 2
    assert "semantic.a" in projected["source_contracts"]
    assert projected["projection_metadata"]["contract_version"] == "semantic-context-v8"

@pytest.mark.asyncio
async def test_typed_revision_uses_compact_revision_agent_instead_of_primary_generator() -> None:
    primary = CapturingLLM()
    repair = CapturingLLM()
    revision = CapturingLLM()
    agent = SqlGenerationSkill(
        primary,
        dialect="postgres",
        max_result_rows=500,
        repair_llm=repair,
        revision_llm=revision,
    )
    previous_sql = (
        "SELECT city, SUM(approved_count) AS approved_count "
        "FROM semantic.v_daily_payment_metrics GROUP BY city LIMIT 400"
    )
    semantic_context = {
        "allowed_sources": ["semantic.v_daily_payment_metrics"],
        "query_policy": {"maximum_rows": 500, "timezone": "America/Lima"},
        "source_contracts": {
            "semantic.v_daily_payment_metrics": {
                "name": "daily_payment_metrics",
                "source": "semantic.v_daily_payment_metrics",
                "columns": ["metric_date", "city", "approved_count", "transaction_count"],
                "measures": [{"name": "approved_count", "column": "approved_count"}],
            }
        },
        "calendar_context": {"timezone": "America/Lima"},
        "semantic_symbols": {
            "metrics": [{"name": "approved_count", "column": "approved_count"}],
            "dimensions": [{"name": "city", "column": "city"}],
            "sources": [{"name": "daily", "source": "semantic.v_daily_payment_metrics"}],
        },
        "catalog_hits": [{"content": "large retrieval prose" * 1000}],
        "selected_examples": [{"sql": "SELECT ..." * 1000}],
    }

    await agent.generate(
        question="Agrega el canal y conserva la tasa por ciudad",
        semantic_context=semantic_context,
        history=[{"role": "assistant", "content": "old result" * 1000}],
        structured_memory={
            "last_interpretation": "Tasa por ciudad",
            "last_metrics": ["approved_count"],
            "last_dimensions": ["city"],
            "last_source_objects": ["semantic.v_daily_payment_metrics"],
            "last_sql": previous_sql,
        },
        feedback="Agrega el canal",
        previous_sql=previous_sql,
        feedback_plan={
            "strategy": "regenerate",
            "changes": [
                {
                    "change_id": "change_1",
                    "change_type": "add_dimension",
                    "target": "channel",
                    "required": True,
                }
            ],
        },
    )

    assert primary.calls == []
    assert repair.calls == []
    assert len(revision.calls) == 1
    payload = json.loads(revision.calls[0]["user"])
    assert "catalog_hits" not in payload["revision_context"]
    assert "selected_examples" not in payload["revision_context"]
    assert payload["previous_sql"] == previous_sql
    estimated = PromptBudget.estimate_tokens(revision.calls[0]["system"]) + PromptBudget.estimate_tokens(
        revision.calls[0]["user"]
    )
    assert estimated + 1800 < 7000


def test_semantic_projection_preserves_required_previous_source_for_revision() -> None:
    projector = SemanticContextProjector(max_source_contracts=2)
    full_context = {
        "allowed_sources": ["semantic.a", "semantic.b", "semantic.c"],
        "source_contracts": {
            "semantic.a": {"name": "approval", "columns": ["approved_count"]},
            "semantic.b": {"name": "declines", "columns": ["declined_count"]},
            "semantic.c": {"name": "previous_source", "columns": ["legacy_metric"]},
        },
        "semantic_symbols": {"metrics": [], "dimensions": [], "sources": []},
        "catalog_hits": [
            {
                "score": 10,
                "kind": "entity",
                "path": "approval.yaml",
                "content": {"name": "approval", "source": "semantic.a"},
            }
        ],
        "selected_examples": [
            {"question": "approval", "sql": "SELECT * FROM semantic.a"}
        ],
    }

    projected = projector.project(
        question="approval",
        full_context=full_context,
        required_sources=["semantic.c"],
    )

    assert "semantic.c" in projected["source_contracts"]
    assert len(projected["source_contracts"]) == 2
