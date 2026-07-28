from pathlib import Path

from axiz.pe.sql_agent.models.contracts import CostValidation, SecurityValidation
from axiz.pe.sql_agent.services.llm import AgentModelRegistry
from axiz.pe.sql_agent.tools.llm_token_estimator import LLMApprovalTokenEstimator


def test_estimate_covers_only_post_approval_llm_calls(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
default:
  provider: openai
  model: test-default
  model_context_limit_tokens: 32000
  context_window_tokens: 32000
  max_input_tokens: 16000
  max_output_tokens: 1000
agents:
  evidence_reviewer:
    model: reviewer-model
    max_output_tokens: 1400
  investigation_coordinator:
    model: coordinator-model
    max_output_tokens: 1800
""".strip(),
        encoding="utf-8",
    )
    estimator = LLMApprovalTokenEstimator(AgentModelRegistry(config), max_result_rows=500)

    estimate = estimator.estimate(
        question="¿Cuál fue el monto procesado por MCC?",
        interpretation="Monto diario por categoría",
        sql="SELECT mcc, metric_date, processed_amount_pen FROM semantic.metrics LIMIT 500",
        security=SecurityValidation(
            approved=True,
            normalized_sql="SELECT 1 LIMIT 500",
            columns=["mcc", "metric_date", "processed_amount_pen"],
            max_rows=500,
            enforced_limit=500,
        ),
        cost=CostValidation(
            approved=True,
            plan_rows=500,
            plan_width=48,
            max_node_rows=250000,
        ),
    )

    assert estimate.expected_call_count == 2
    assert [call.agent for call in estimate.calls] == ["evidence_reviewer", "evidence_reviewer"]
    assert estimate.projected_result_rows == 500
    assert estimate.estimated_total_tokens > 0
    assert estimate.maximum_total_tokens >= estimate.estimated_total_tokens
    assert estimate.calls[0].model == "reviewer-model"
    assert estimate.calls[1].model == "reviewer-model"


def test_autonomous_estimate_reserves_critic_supervisor_and_synthesis(tmp_path: Path) -> None:
    config = tmp_path / "agents.yaml"
    config.write_text(
        """
default:
  provider: openai
  model: test-default
  model_context_limit_tokens: 32000
  context_window_tokens: 32000
  max_input_tokens: 16000
  max_output_tokens: 1200
agents:
  evidence_reviewer: {}
  investigation_coordinator: {}
""".strip(),
        encoding="utf-8",
    )
    estimator = LLMApprovalTokenEstimator(AgentModelRegistry(config), max_result_rows=500)
    estimate = estimator.estimate(
        question="Investiga la variación",
        interpretation="Análisis gobernado",
        sql="SELECT 1 LIMIT 10",
        security=SecurityValidation(
            approved=True,
            columns=["value"],
            max_rows=500,
            enforced_limit=10,
        ),
        cost=CostValidation(approved=True, plan_rows=10, plan_width=32),
        autonomous=True,
        existing_evidence_count=1,
    )
    assert estimate.expected_call_count == 5
    assert [call.agent for call in estimate.calls] == [
        "evidence_reviewer",
        "evidence_reviewer",
        "evidence_reviewer",
        "investigation_coordinator",
        "investigation_coordinator",
    ]
