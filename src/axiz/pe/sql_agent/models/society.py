from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from axiz.pe.sql_agent.models.query_spec import (
    CompiledSqlArtifact,
    QuerySpecPatch,
    QuerySpecReference,
    SemanticQuerySpec,
)


class SocietyRole(StrEnum):
    INVESTIGATION_COORDINATOR = "investigation_coordinator"
    DOMAIN_ANALYST = "domain_analyst"
    SQL_ENGINEER = "sql_engineer"
    EVIDENCE_REVIEWER = "evidence_reviewer"


class CoordinatorInvocation(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    context_relation: str | None = None
    memory_summary: dict[str, Any] = Field(default_factory=dict)
    published_domains: list[dict[str, Any]] = Field(default_factory=list)
    specialist_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: list[dict[str, Any]] = Field(default_factory=list)
    governed_budget: dict[str, Any] = Field(default_factory=dict)


class CoordinatorResult(BaseModel):
    mode: Literal[
        "context",
        "route",
        "plan",
        "supervise",
        "synthesize",
        "clarify",
    ]
    route: str | None = None
    selected_specialist: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)
    action: str | None = None
    clarification_question: str | None = None
    answer: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class DomainAnalystInvocation(BaseModel):
    task: dict[str, Any]
    profile: dict[str, Any]
    original_question: str
    memory_summary: dict[str, Any] = Field(default_factory=dict)
    published_domains: list[dict[str, Any]] = Field(default_factory=list)
    prior_evidence: list[dict[str, Any]] = Field(default_factory=list)


class DomainAnalystResult(BaseModel):
    task_id: str
    specialist: str
    refined_question: str
    domain: str | None = None
    expected_evidence: list[str] = Field(default_factory=list)
    catalog_focus: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    can_proceed: bool = True
    block_reason: str | None = None


class SqlEngineerInvocation(BaseModel):
    mode: Literal["generate", "interpret_feedback", "revise", "repair"]
    question: str
    analytical_contract: dict[str, Any] = Field(default_factory=dict)
    semantic_context: dict[str, Any] = Field(default_factory=dict)
    previous_sql: str | None = None
    feedback: str | None = None
    validator_feedback: dict[str, Any] = Field(default_factory=dict)
    raw_user_message: str | None = None
    query_spec_ref: QuerySpecReference | None = None
    query_spec_patch: QuerySpecPatch | None = None
    semantic_query_spec: SemanticQuerySpec | None = None


class SqlEngineerResult(BaseModel):
    mode: Literal["generate", "interpret_feedback", "revise", "repair"]
    interpretation: str = ""
    sql: str | None = None
    feedback_plan: dict[str, Any] = Field(default_factory=dict)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    source_objects: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: str | None = None
    query_spec_ref: QuerySpecReference | None = None
    query_spec_patch: QuerySpecPatch | None = None
    semantic_query_spec: SemanticQuerySpec | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None


class EvidenceReviewerInvocation(BaseModel):
    mode: Literal["verify", "criticize", "explain"]
    question: str
    interpretation: str = ""
    sql: str | None = None
    raw_user_message: str | None = None
    query_spec_ref: QuerySpecReference | None = None
    semantic_query_spec: SemanticQuerySpec | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class EvidenceReviewerResult(BaseModel):
    mode: Literal["verify", "criticize", "explain"]
    valid: bool | None = None
    ready_to_finalize: bool | None = None
    accepted_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    answer: str | None = None
    findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SocietyRoleContract(BaseModel):
    role: SocietyRole
    purpose: str
    modes: list[str]
    invoked_when: list[str]
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    may_call_llm: bool = True
    may_execute_sql: bool = False
    prohibited_actions: list[str] = Field(default_factory=list)


def society_role_contracts() -> list[SocietyRoleContract]:
    common_prohibitions = [
        "bypass SQL security",
        "bypass query-cost validation",
        "bypass HITL",
        "change governed budgets",
        "change database permissions",
    ]
    return [
        SocietyRoleContract(
            role=SocietyRole.INVESTIGATION_COORDINATOR,
            purpose="Resolve context, route, plan, supervise, replan and synthesize investigations.",
            modes=["context", "route", "plan", "supervise", "synthesize"],
            invoked_when=[
                "at the start of every user turn",
                "when evidence is incomplete or contradictory",
                "when a full investigation must be planned or finalized",
            ],
            input_contract=CoordinatorInvocation.model_json_schema(),
            output_contract=CoordinatorResult.model_json_schema(),
            prohibited_actions=common_prohibitions + ["generate SQL", "execute tools directly"],
        ),
        SocietyRoleContract(
            role=SocietyRole.DOMAIN_ANALYST,
            purpose="Refine one delegated analytical task using a capability profile and catalog scope.",
            modes=["prepare", "risk_review"],
            invoked_when=["the coordinator delegates a task to a matching capability profile"],
            input_contract=DomainAnalystInvocation.model_json_schema(),
            output_contract=DomainAnalystResult.model_json_schema(),
            prohibited_actions=common_prohibitions + ["execute SQL", "invent unpublished metrics"],
        ),
        SocietyRoleContract(
            role=SocietyRole.SQL_ENGINEER,
            purpose="Generate, interpret feedback, revise and repair SQL from governed contracts.",
            modes=["generate", "interpret_feedback", "revise", "repair"],
            invoked_when=[
                "an analytical contract needs SQL",
                "a user requests changes",
                "a deterministic validator returns repair feedback",
            ],
            input_contract=SqlEngineerInvocation.model_json_schema(),
            output_contract=SqlEngineerResult.model_json_schema(),
            prohibited_actions=common_prohibitions + ["execute SQL", "select unpublished sources"],
        ),
        SocietyRoleContract(
            role=SocietyRole.EVIDENCE_REVIEWER,
            purpose="Verify results, criticize accumulated evidence and explain accepted findings.",
            modes=["verify", "criticize", "explain"],
            invoked_when=[
                "a query result is available",
                "the coordinator needs a sufficiency decision",
                "accepted evidence must be explained to the user",
            ],
            input_contract=EvidenceReviewerInvocation.model_json_schema(),
            output_contract=EvidenceReviewerResult.model_json_schema(),
            prohibited_actions=common_prohibitions + ["generate or execute SQL", "invent evidence"],
        ),
    ]


class FeedbackOperation(StrEnum):
    SET = "set"
    INCREASE = "increase"
    DECREASE = "decrease"
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"
    REORDER = "reorder"
    REGENERATE = "regenerate"


class FeedbackTarget(StrEnum):
    LIMIT = "limit"
    TIME_WINDOW = "time_window"
    FILTER = "filter"
    ORDER = "order"
    GROUPING = "grouping"
    DIMENSION = "dimension"
    METRIC = "metric"
    SOURCE = "source"
    PROJECTION = "projection"
    COMPARISON = "comparison"
    OTHER = "other"


class FeedbackUnit(StrEnum):
    ROWS = "rows"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"
    NONE = "none"


class FeedbackIntent(BaseModel):
    change_id: str = Field(min_length=1, max_length=80)
    operation: FeedbackOperation
    target: FeedbackTarget
    value: int | float | str | None = None
    unit: FeedbackUnit = FeedbackUnit.NONE
    field: str | None = None
    previous_field: str | None = None
    operator: str | None = None
    values: list[str] = Field(default_factory=list, max_length=20)
    scope: str = "overall"
    representation: str | None = None
    rationale: str = Field(default="", max_length=300)


class FeedbackIntentPlan(BaseModel):
    summary: str = Field(default="", max_length=500)
    intents: list[FeedbackIntent] = Field(default_factory=list, max_length=8)
    preserve: list[str] = Field(default_factory=list, max_length=16)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=1.0, ge=0, le=1)
