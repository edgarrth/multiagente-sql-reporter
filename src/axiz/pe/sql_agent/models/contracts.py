from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Intent(StrEnum):
    ANALYTICAL_QUERY = "analytical_query"
    CATALOG_QUESTION = "catalog_question"
    CAPABILITY_QUESTION = "capability_question"
    UNSUPPORTED = "unsupported"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class RunStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    NEEDS_CLARIFICATION = "needs_clarification"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class UserPrincipal(BaseModel):
    user_id: UUID
    username: str
    roles: list[str]
    auth_source: str = "local"


class SessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SessionDeleteResponse(BaseModel):
    id: UUID
    deleted: bool = True


class SessionResponse(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    pending_run_id: UUID | None = None
    message_count: int = 0


class ChatMessageResponse(BaseModel):
    id: int
    session_id: UUID
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AgentRunRequest(BaseModel):
    session_id: UUID
    question: str = Field(min_length=2, max_length=4000)


class HumanFeedbackRequest(BaseModel):
    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_change_comment(self) -> "HumanFeedbackRequest":
        if self.decision == ApprovalDecision.REQUEST_CHANGES and not (self.comment or "").strip():
            raise ValueError("A comment is required when requesting SQL changes")
        return self


class IntentDomainOutput(BaseModel):
    intent: Intent
    domain: str | None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    clarification_question: str | None = None


class CatalogAnswerOutput(BaseModel):
    answer: str
    caveats: list[str] = Field(default_factory=list)


class SqlGenerationOutput(BaseModel):
    sql: str
    interpretation: str
    assumptions: list[str] = Field(default_factory=list)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    source_objects: list[str] = Field(default_factory=list)


class SecurityValidation(BaseModel):
    approved: bool
    normalized_sql: str | None = None
    violations: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    statement_type: str | None = None
    max_rows: int | None = None
    enforced_limit: int | None = None
    required_filter_columns: list[str] = Field(default_factory=list)
    denied_schemas: list[str] = Field(default_factory=list)
    denied_functions: list[str] = Field(default_factory=list)
    reject_cross_joins: bool = True


class CostValidation(BaseModel):
    approved: bool
    total_cost: float | None = None
    plan_rows: int | None = None
    plan_width: int | None = None
    max_node_rows: int | None = None
    plan_node_count: int = 0
    relation_bytes: int | None = None
    warnings: list[str] = Field(default_factory=list)
    explain_plan: dict[str, Any] | list[Any] | None = None
    tables: list[str] = Field(default_factory=list)
    plan_relations: list[str] = Field(default_factory=list)
    max_plan_cost: float | None = None
    max_plan_rows: int | None = None
    max_relation_bytes: int | None = None
    timeout_seconds: int | None = None


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    elapsed_ms: float
    truncated: bool = False


class LLMCallUsage(BaseModel):
    call_id: str
    agent: str
    provider: str
    model: str
    status: str = "completed"
    estimated_input_tokens: int = 0
    reserved_output_tokens: int = 0
    estimated_max_total_tokens: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    duration_ms: float | None = None
    attempt_count: int = 1
    error: str | None = None


class LLMUsageSummary(BaseModel):
    call_count: int = 0
    completed_calls: int = 0
    failed_calls: int = 0
    estimated_input_tokens: int = 0
    reserved_output_tokens: int = 0
    estimated_max_total_tokens: int = 0
    actual_input_tokens: int = 0
    actual_output_tokens: int = 0
    actual_total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    actual_usage_complete: bool = True
    calls: list[LLMCallUsage] = Field(default_factory=list)


class LLMPlannedCallEstimate(BaseModel):
    agent: str
    provider: str
    model: str
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_total_tokens: int = 0
    max_output_tokens: int = 0
    maximum_total_tokens: int = 0
    basis: str


class LLMApprovalEstimate(BaseModel):
    expected_call_count: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_total_tokens: int = 0
    maximum_total_tokens: int = 0
    projected_result_rows: int = 0
    projected_row_width_bytes: int = 0
    assumptions: list[str] = Field(default_factory=list)
    calls: list[LLMPlannedCallEstimate] = Field(default_factory=list)


class ExcelExportAvailability(BaseModel):
    available: bool
    reason: str | None = None
    row_count: int = 0
    truncated: bool = False
    format: str = "xlsx"


class VerificationOutput(BaseModel):
    valid: bool
    confidence: float = Field(ge=0, le=1)
    observations: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class VisualizationSpec(BaseModel):
    type: str = "table"
    title: str
    x: str | None = None
    y: list[str] = Field(default_factory=list)


class ExplanationOutput(BaseModel):
    answer: str
    key_findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    visualization: VisualizationSpec


class ReviewPayload(BaseModel):
    run_id: UUID
    revision: int = 1
    question: str
    domain: str
    interpretation: str
    sql: str
    assumptions: list[str]
    source_objects: list[str]


class AgentTraceStep(BaseModel):
    stage: str
    label: str
    detail: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


class RunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: UUID
    session_id: UUID
    status: RunStatus
    review: ReviewPayload | None = None
    interpretation: str | None = None
    domain: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    source_objects: list[str] = Field(default_factory=list)
    answer: str | None = None
    key_findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    result: QueryResult | None = None
    visualization: VisualizationSpec | None = None
    sql: str | None = None
    error: str | None = None
    trace: list[AgentTraceStep] = Field(default_factory=list)
    security_validation: SecurityValidation | None = None
    cost_validation: CostValidation | None = None
    llm_usage: LLMUsageSummary | None = None
    llm_approval_estimate: LLMApprovalEstimate | None = None
    export: ExcelExportAvailability | None = None


class TeamsMessageRequest(BaseModel):
    channel_user_id: str
    display_name: str | None = None
    conversation_id: str
    text: str = Field(min_length=1, max_length=4000)


class TeamsMessageResponse(BaseModel):
    text: str
    awaiting_approval: bool = False
    run_id: UUID | None = None
