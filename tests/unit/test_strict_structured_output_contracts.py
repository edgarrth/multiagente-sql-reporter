from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from axiz.pe.sql_agent.models.contracts import (
    AutonomousRoutingDecision,
    AutonomousSynthesisOutput,
    CatalogAnswerOutput,
    ContextResolutionOutput,
    ConversationAnswerOutput,
    CriticReviewOutput,
    ExplanationOutput,
    FeedbackSemanticComplianceOutput,
    IntentDomainOutput,
    InvestigationPlan,
    SpecialistProposalReview,
    SpecialistTaskOutput,
    SqlGenerationOutput,
    SupervisorDecision,
    VerificationOutput,
)
from axiz.pe.sql_agent.models.query_spec import (
    CompiledSqlArtifact,
    CompiledSqlValidation,
    QuerySpecReference,
)
from axiz.pe.sql_agent.models.society import FeedbackIntentPlan
from axiz.pe.sql_agent.services.structured_output_schema import (
    ensure_closed_response_schema,
    incompatible_open_object_paths,
)


LLM_RESPONSE_MODELS = [
    AutonomousRoutingDecision,
    AutonomousSynthesisOutput,
    CatalogAnswerOutput,
    ContextResolutionOutput,
    ConversationAnswerOutput,
    CriticReviewOutput,
    ExplanationOutput,
    FeedbackIntentPlan,
    FeedbackSemanticComplianceOutput,
    IntentDomainOutput,
    InvestigationPlan,
    SpecialistProposalReview,
    SpecialistTaskOutput,
    SqlGenerationOutput,
    SupervisorDecision,
    VerificationOutput,
]


@pytest.mark.parametrize("response_model", LLM_RESPONSE_MODELS)
def test_llm_response_models_do_not_expose_open_json_objects(
    response_model: type[BaseModel],
) -> None:
    assert incompatible_open_object_paths(response_model) == []
    ensure_closed_response_schema(response_model)


def test_sql_generation_output_excludes_runtime_artifact() -> None:
    schema = SqlGenerationOutput.model_json_schema()
    assert "compiled_sql_artifact" not in schema["properties"]


def test_compiled_sql_validation_is_closed_and_deterministic() -> None:
    schema = CompiledSqlValidation.model_json_schema()
    assert schema["additionalProperties"] is False

    artifact = CompiledSqlArtifact(
        query_spec_ref=QuerySpecReference(id="qs-test", version=1),
        dialect="postgres",
        sql="SELECT 1 LIMIT 1",
        sql_hash="sha256:test",
        validation={
            "parse_valid": True,
            "order_dependencies_valid": True,
            "query_spec_alignment_valid": True,
            "violations": [],
        },
    )
    assert artifact.validation.parse_valid is True
    assert artifact.validation.violations == []


def test_schema_guard_rejects_dict_any_response_fields() -> None:
    class InvalidResponse(BaseModel):
        validation: dict[str, object] = Field(default_factory=dict)

    issues = incompatible_open_object_paths(InvalidResponse)
    assert issues
    with pytest.raises(ValueError, match="open JSON objects"):
        ensure_closed_response_schema(InvalidResponse)
