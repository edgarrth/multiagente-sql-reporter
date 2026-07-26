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
    CONVERSATION_QUESTION = "conversation_question"
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
    CANCELLED = "cancelled"


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
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class HumanFeedbackRequest(BaseModel):
    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def require_change_comment(self) -> "HumanFeedbackRequest":
        if self.decision == ApprovalDecision.REQUEST_CHANGES and not (self.comment or "").strip():
            raise ValueError("A comment is required when requesting SQL changes")
        return self




class QueryFilter(BaseModel):
    field: str
    operator: str
    value: str
    source: str = "user"


class TimeWindowContext(BaseModel):
    label: str | None = None
    start_expression: str | None = None
    end_expression: str | None = None
    grain: str | None = None
    closed_period: bool | None = None


class ContextResolutionOutput(BaseModel):
    original_question: str
    resolved_question: str
    is_follow_up: bool = False
    inherited_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    requires_clarification: bool = False
    clarification_question: str | None = None


class ConversationMemory(BaseModel):
    schema_version: int = 1
    revision: int = 0
    last_run_id: UUID | None = None
    last_status: str | None = None
    last_user_request: str | None = None
    last_resolved_question: str | None = None
    last_interpretation: str | None = None
    last_domain: str | None = None
    last_metrics: list[str] = Field(default_factory=list)
    last_dimensions: list[str] = Field(default_factory=list)
    last_filters: list[QueryFilter] = Field(default_factory=list)
    last_time_window: TimeWindowContext | None = None
    last_sql: str | None = None
    last_result_schema: list[str] = Field(default_factory=list)
    last_result_sample: list[dict[str, Any]] = Field(default_factory=list)
    last_row_count: int | None = None
    last_answer: str | None = None
    last_key_findings: list[str] = Field(default_factory=list)
    last_models: list[str] = Field(default_factory=list)
    last_token_usage: int | None = None
    updated_at: datetime | None = None


class IntentDomainOutput(BaseModel):
    intent: Intent
    domain: str | None
    confidence: float = Field(ge=0, le=1)
    rationale: str
    clarification_question: str | None = None


class CatalogAnswerOutput(BaseModel):
    answer: str
    caveats: list[str] = Field(default_factory=list)


class ConversationAnswerOutput(BaseModel):
    answer: str
    referenced_turns: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class SqlGenerationOutput(BaseModel):
    sql: str
    interpretation: str
    assumptions: list[str] = Field(default_factory=list)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    selected_filters: list[QueryFilter] = Field(default_factory=list)
    time_window: TimeWindowContext | None = None
    source_objects: list[str] = Field(default_factory=list)


class SqlChangeType(StrEnum):
    SET_LIMIT = "set_limit"
    ADD_FILTER = "add_filter"
    REMOVE_FILTER = "remove_filter"
    REPLACE_FILTER = "replace_filter"
    CHANGE_TIME_WINDOW = "change_time_window"
    ADD_DIMENSION = "add_dimension"
    REMOVE_DIMENSION = "remove_dimension"
    CHANGE_GROUPING = "change_grouping"
    CHANGE_ORDER = "change_order"
    ADD_METRIC = "add_metric"
    REMOVE_METRIC = "remove_metric"
    REPLACE_METRIC = "replace_metric"
    REPLACE_SOURCE = "replace_source"
    SEMANTIC_REGENERATION = "semantic_regeneration"


class SqlFeedbackStrategy(StrEnum):
    AST_ONLY = "ast_only"
    REGENERATE = "regenerate"
    HYBRID = "hybrid"
    CLARIFICATION = "clarification"


class SqlSortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class SqlChangeRequest(BaseModel):
    change_id: str = Field(min_length=1, max_length=80)
    change_type: SqlChangeType
    target: str | None = None
    previous_target: str | None = None
    operator: str | None = None
    value: str | None = None
    previous_value: str | None = None
    values: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    direction: SqlSortDirection | None = None
    predicate_sql: str | None = None
    required: bool = True
    deterministic_candidate: bool = False
    rationale: str = ""


class SqlFeedbackPlan(BaseModel):
    feedback: str = ""
    summary: str = ""
    strategy: SqlFeedbackStrategy = SqlFeedbackStrategy.HYBRID
    changes: list[SqlChangeRequest] = Field(default_factory=list)
    requires_regeneration: bool = True
    requires_clarification: bool = False
    clarification_question: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class FeedbackComplianceCheck(BaseModel):
    change_id: str
    change_type: SqlChangeType
    supported_deterministically: bool = False
    passed: bool | None = None
    evidence: str | None = None


class FeedbackSemanticComplianceOutput(BaseModel):
    compliant: bool
    applied_changes: list[str] = Field(default_factory=list)
    missing_changes: list[str] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    rationale: str = ""
    requires_clarification: bool = False
    clarification_question: str | None = None


class FeedbackComplianceResult(BaseModel):
    compliant: bool
    deterministic_compliant: bool = True
    semantic_compliant: bool = True
    requested_changes: list[str] = Field(default_factory=list)
    applied_changes: list[str] = Field(default_factory=list)
    missing_changes: list[str] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    checks: list[FeedbackComplianceCheck] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    requires_clarification: bool = False
    clarification_question: str | None = None
    retry_instruction: str | None = None


class SqlFeedbackApplication(BaseModel):
    sql: str
    requested_limit: int | None = None
    applied_limit: int | None = None
    previous_limit: int | None = None
    changed: bool = False
    applied_changes: list[str] = Field(default_factory=list)
    deferred_changes: list[str] = Field(default_factory=list)
    failed_changes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    engine: str | None = None
    dialect: str | None = None
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
    engine: str | None = None
    dialect: str | None = None
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


class ModelValidationItem(BaseModel):
    agent: str
    provider: str
    model: str
    status: str
    catalog_available: bool | None = None
    structured_output_supported: bool | None = None
    context_limit_tokens: int | None = None
    latency_ms: float | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ModelValidationReport(BaseModel):
    mode: str
    failure_policy: str
    ready: bool
    checked_at: datetime
    unique_model_count: int = 0
    valid_count: int = 0
    warning_count: int = 0
    invalid_count: int = 0
    skipped_count: int = 0
    items: list[ModelValidationItem] = Field(default_factory=list)


class RunCancelResponse(BaseModel):
    run_id: UUID
    status: str
    cancel_requested: bool = True


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
    resolved_question: str | None = None
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
    resolved_question: str | None = None
    context_resolution: ContextResolutionOutput | None = None
    memory_revision: int | None = None
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
    feedback_plan: SqlFeedbackPlan | None = None
    feedback_application: SqlFeedbackApplication | None = None
    feedback_compliance: FeedbackComplianceResult | None = None
    export: ExcelExportAvailability | None = None
    run_version: int | None = None
    idempotent_replay: bool = False


class TeamsMessageRequest(BaseModel):
    channel_user_id: str
    display_name: str | None = None
    conversation_id: str
    text: str = Field(min_length=1, max_length=4000)


class TeamsMessageResponse(BaseModel):
    text: str
    awaiting_approval: bool = False
    run_id: UUID | None = None
