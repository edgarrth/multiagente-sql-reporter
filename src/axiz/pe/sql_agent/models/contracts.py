from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from axiz.pe.sql_agent.models.query_spec import (
    CompiledSqlArtifact,
    QuerySpecPatch,
    QuerySpecPatchOperation,
    QuerySpecReference,
    SemanticQuerySpec,
)


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


class ContextRelation(StrEnum):
    """Semantic relationship between the current message and prior analytical state."""

    INDEPENDENT_REQUEST = "independent_request"
    ANALYTICAL_FOLLOW_UP = "analytical_follow_up"
    SESSION_REFERENCE = "session_reference"
    AMBIGUOUS = "ambiguous"


class ContextResolutionOutput(BaseModel):
    original_question: str
    resolved_question: str
    relation: ContextRelation = ContextRelation.INDEPENDENT_REQUEST
    is_follow_up: bool = False
    requires_sql_revision: bool = False
    inherited_fields: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    rationale: str = ""
    requires_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def normalize_relation_flags(self) -> "ContextResolutionOutput":
        analytical_follow_up = self.relation == ContextRelation.ANALYTICAL_FOLLOW_UP
        self.is_follow_up = analytical_follow_up
        # A follow-up is not necessarily a SQL revision. When a previous attempt failed before
        # producing approved SQL, the resolver may still reconstruct a standalone analytical
        # request from recent conversation and route it through normal SQL generation.
        if not analytical_follow_up:
            self.requires_sql_revision = False
            self.inherited_fields = []
        if self.requires_clarification:
            self.requires_sql_revision = False
        return self


class ConversationMemory(BaseModel):
    schema_version: int = 5
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
    last_ordering: list[str] = Field(default_factory=list)
    last_limit: int | None = None
    last_source_objects: list[str] = Field(default_factory=list)
    last_sql: str | None = None
    last_result_schema: list[str] = Field(default_factory=list)
    last_result_sample: list[dict[str, Any]] = Field(default_factory=list)
    last_row_count: int | None = None
    last_answer: str | None = None
    last_key_findings: list[str] = Field(default_factory=list)
    last_models: list[str] = Field(default_factory=list)
    last_token_usage: int | None = None
    last_investigation: dict[str, Any] = Field(default_factory=dict)
    last_attempt_run_id: UUID | None = None
    last_attempt_status: str | None = None
    last_attempt_user_request: str | None = None
    last_attempt_error: str | None = None
    pending_revision_feedback: str | None = None
    pending_revision_plan: dict[str, Any] = Field(default_factory=dict)
    last_query_spec: SemanticQuerySpec | None = None
    last_compiled_sql_artifact: CompiledSqlArtifact | None = None
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
    query_spec_ref: QuerySpecReference | None = None
    change_summary: list[str] = Field(default_factory=list, max_length=20)
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=800)
    # CompiledSqlArtifact is intentionally not part of this LLM response.
    # It is created deterministically after SQL parsing and query-spec validation.


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


class SqlTemporalScope(StrEnum):
    """Semantic target of a temporal revision.

    ``overall_window`` is eligible for deterministic AST rewriting only when a single governed
    window is proven. Comparative scopes intentionally require regeneration because changing a
    baseline or adding period buckets can alter projections and aggregation semantics.
    """

    OVERALL_WINDOW = "overall_window"
    CURRENT_PERIOD = "current_period"
    COMPARISON_BASELINE = "comparison_baseline"
    COMPARISON_SERIES = "comparison_series"
    ALL_PERIODS = "all_periods"


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
    time_window_delta_months: int | None = Field(default=None, ge=-120, le=120)
    time_window_months: int | None = Field(default=None, ge=1, le=120)
    time_window_delta_days: int | None = Field(default=None, ge=-3650, le=3650)
    time_window_days: int | None = Field(default=None, ge=1, le=3650)
    time_window_scope: SqlTemporalScope = SqlTemporalScope.OVERALL_WINDOW
    comparison_periods: int | None = Field(default=None, ge=1, le=120)
    direction: SqlSortDirection | None = None
    predicate_sql: str | None = None
    required: bool = True
    deterministic_candidate: bool = False
    rationale: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_time_window_contract(self) -> "SqlChangeRequest":
        month_fields = (
            self.time_window_delta_months,
            self.time_window_months,
        )
        day_fields = (
            self.time_window_delta_days,
            self.time_window_days,
        )
        if any(value is not None for value in month_fields) and any(
            value is not None for value in day_fields
        ):
            raise ValueError("A time-window change cannot mix month and day fields")
        if self.time_window_delta_months is not None and self.time_window_months is not None:
            raise ValueError("A month window cannot define an absolute value and delta together")
        if self.time_window_delta_days is not None and self.time_window_days is not None:
            raise ValueError("A day window cannot define an absolute value and delta together")
        if self.comparison_periods is not None:
            if self.change_type != SqlChangeType.CHANGE_TIME_WINDOW:
                raise ValueError("comparison_periods is valid only for change_time_window")
            if self.time_window_scope not in {
                SqlTemporalScope.COMPARISON_BASELINE,
                SqlTemporalScope.COMPARISON_SERIES,
            }:
                raise ValueError(
                    "comparison_periods requires comparison_baseline or comparison_series scope"
                )
        if (
            self.time_window_scope != SqlTemporalScope.OVERALL_WINDOW
            and self.change_type != SqlChangeType.CHANGE_TIME_WINDOW
        ):
            raise ValueError("time_window_scope is valid only for change_time_window")
        return self


class SqlFeedbackPlan(BaseModel):
    feedback: str = Field(default="", max_length=4000)
    summary: str = Field(default="", max_length=800)
    strategy: SqlFeedbackStrategy = SqlFeedbackStrategy.HYBRID
    changes: list[SqlChangeRequest] = Field(default_factory=list, max_length=8)
    requires_regeneration: bool = True
    requires_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=800)
    confidence: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=8)
    raw_user_message: str = Field(default="", max_length=4000)
    query_spec_ref: QuerySpecReference | None = None
    query_spec_patch: QuerySpecPatch | None = None
    resolved_query_spec: SemanticQuerySpec | None = None
    derived_changes: list[QuerySpecPatchOperation] = Field(default_factory=list)


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
    failed_sql: str | None = None


class SqlFeedbackApplication(BaseModel):
    sql: str
    requested_limit: int | None = None
    applied_limit: int | None = None
    previous_limit: int | None = None
    requested_time_window_delta_months: int | None = None
    previous_time_window_months: int | None = None
    applied_time_window_months: int | None = None
    requested_time_window_delta_days: int | None = None
    previous_time_window_days: int | None = None
    applied_time_window_days: int | None = None
    changed: bool = False
    applied_changes: list[str] = Field(default_factory=list)
    deferred_changes: list[str] = Field(default_factory=list)
    failed_changes: list[str] = Field(default_factory=list)
    preserved_invariants: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TaskBudget(BaseModel):
    """Hard per-task limits enforced outside the LLM."""

    max_attempts: int = Field(default=3, ge=1, le=10)
    max_replans: int = Field(default=1, ge=0, le=10)
    max_llm_tokens: int = Field(default=24_000, ge=1)
    max_queries: int = Field(default=1, ge=1, le=10)
    max_active_seconds: int = Field(default=180, ge=1)
    max_plan_cost_total: float = Field(default=150_000, ge=0)
    max_plan_rows_total: int = Field(default=250_000, ge=0)
    max_relation_bytes_total: int = Field(default=512 * 1024 * 1024, ge=0)


class TaskBudgetUsage(BaseModel):
    # Reserved executable-query slots. EXPLAIN and SQL repair attempts are tracked separately.
    attempts: int = 0
    replans: int = 0
    llm_tokens: int = 0
    queries: int = 0
    active_seconds: float = 0.0
    plan_cost_total: float = 0.0
    plan_rows_total: int = 0
    relation_bytes_total: int = 0
    exhausted_reasons: list[str] = Field(default_factory=list)


class SpecialistProposalStatus(StrEnum):
    READY = "ready"
    CACHE_HIT = "cache_hit"
    AWAITING_HITL = "awaiting_hitl"
    APPROVED = "approved"
    EXECUTED = "executed"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    FAILED = "failed"


class SpecialistProposalReview(BaseModel):
    approved: bool
    task_alignment: bool = True
    catalog_alignment: bool = True
    evidence_sufficient: bool = True
    missing_requirements: list[str] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    retry_instruction: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)
    review_mode: str = "llm"
    review_reasons: list[str] = Field(default_factory=list)


class SpecialistQueryProposal(BaseModel):
    proposal_id: str
    task_id: str
    specialist_id: str
    wave: int = 0
    status: SpecialistProposalStatus = SpecialistProposalStatus.READY
    question: str
    domain: str | None = None
    interpretation: str = ""
    sql: str = ""
    assumptions: list[str] = Field(default_factory=list)
    selected_metrics: list[str] = Field(default_factory=list)
    selected_dimensions: list[str] = Field(default_factory=list)
    selected_filters: list[QueryFilter] = Field(default_factory=list)
    time_window: TimeWindowContext | None = None
    source_objects: list[str] = Field(default_factory=list)
    semantic_context: dict[str, Any] = Field(default_factory=dict)
    security_validation: dict[str, Any] = Field(default_factory=dict)
    cost_validation: dict[str, Any] = Field(default_factory=dict)
    review: SpecialistProposalReview | None = None
    task_budget: TaskBudget = Field(default_factory=TaskBudget)
    task_budget_usage: TaskBudgetUsage = Field(default_factory=TaskBudgetUsage)
    cache_hit: bool = False
    cache_key: str | None = None
    block_reason: str | None = None
    query_spec: SemanticQuerySpec | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None


class EvidenceBackedFinding(BaseModel):
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)


class InvestigationTrajectoryEvent(BaseModel):
    sequence: int = Field(ge=0)
    stage: str
    actor: str
    action: str
    task_id: str | None = None
    specialist_id: str | None = None
    wave: int | None = None
    cache_hit: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpecialistRole(StrEnum):
    ACQUIRING = "acquiring"
    ISSUING = "issuing"
    FRAUD = "fraud"
    CHARGEBACKS = "chargebacks"
    TEMPORAL = "temporal"
    CRITIC = "critic"


class InvestigationQueryMode(StrEnum):
    NEW_EVIDENCE = "new_evidence"
    REVISE_PREVIOUS = "revise_previous"


class InvestigationMode(StrEnum):
    """Adaptive autonomous execution mode selected before planning."""

    DIRECT_SPECIALIST = "direct_specialist"
    FULL_INVESTIGATION = "full_investigation"


class AutonomousRoutingDecision(BaseModel):
    """Semantic routing contract for the governed autonomous society.

    The router decides how much autonomy is needed, not which security or execution
    controls apply. Those controls are immutable and remain outside the LLM.
    """

    mode: InvestigationMode
    specialist: str | None = None
    domain: str | None = None
    task_title: str = Field(default="Análisis solicitado", min_length=1, max_length=200)
    task_objective: str = Field(default="", max_length=1200)
    expected_evidence: list[str] = Field(default_factory=list)
    query_mode: InvestigationQueryMode = InvestigationQueryMode.NEW_EVIDENCE
    complexity_signals: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    requires_clarification: bool = False
    clarification_question: str | None = None

    @model_validator(mode="after")
    def validate_direct_mode(self) -> "AutonomousRoutingDecision":
        if self.mode == InvestigationMode.DIRECT_SPECIALIST and not self.specialist:
            raise ValueError("direct_specialist mode requires one specialist")
        return self


class ProposalReviewDecision(BaseModel):
    """Deterministic decision about whether an additional LLM review is justified."""

    requires_llm_review: bool
    reasons: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)


class InvestigationTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    REJECTED = "rejected"
    FAILED = "failed"


class InvestigationTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=1200)
    specialist: SpecialistRole | str
    domain: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, ge=1, le=100)
    expected_evidence: list[str] = Field(default_factory=list)
    query_mode: InvestigationQueryMode = InvestigationQueryMode.NEW_EVIDENCE
    status: InvestigationTaskStatus = InvestigationTaskStatus.PENDING
    attempts: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)
    wave: int = Field(default=0, ge=0)
    task_budget: TaskBudget = Field(default_factory=TaskBudget)
    task_budget_usage: TaskBudgetUsage = Field(default_factory=TaskBudgetUsage)
    specialist_question: str | None = None
    block_reason: str | None = None


class InvestigationPlan(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    strategy: str = Field(min_length=1, max_length=2000)
    tasks: list[InvestigationTask] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class SpecialistTaskOutput(BaseModel):
    task_id: str
    specialist: SpecialistRole | str
    refined_question: str = Field(min_length=2, max_length=4000)
    domain: str | None = None
    expected_evidence: list[str] = Field(default_factory=list)
    query_mode: InvestigationQueryMode = InvestigationQueryMode.NEW_EVIDENCE
    catalog_focus: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    can_proceed: bool = True
    block_reason: str | None = None


class InvestigationEvidence(BaseModel):
    evidence_id: str
    task_id: str
    specialist: SpecialistRole | str
    question: str
    interpretation: str
    sql: str
    domain: str
    source_objects: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    query_spec_ref: QuerySpecReference | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None


class CriticReviewOutput(BaseModel):
    accepted_evidence_ids: list[str] = Field(default_factory=list)
    rejected_conclusions: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_tasks: list[InvestigationTask] = Field(default_factory=list)
    ready_to_finalize: bool = False
    confidence: float = Field(default=1.0, ge=0, le=1)
    rationale: str = ""


class SupervisorAction(StrEnum):
    DELEGATE = "delegate"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    REJECT_CONCLUSION = "reject_conclusion"
    FINALIZE = "finalize"
    CLARIFY = "clarify"
    STOP_BUDGET = "stop_budget"


class SupervisorDecision(BaseModel):
    action: SupervisorAction
    next_task_id: str | None = None
    next_task_ids: list[str] = Field(default_factory=list)
    new_tasks: list[InvestigationTask] = Field(default_factory=list)
    rejected_conclusions: list[str] = Field(default_factory=list)
    rationale: str = ""
    clarification_question: str | None = None


class AutonomousSynthesisOutput(BaseModel):
    answer: str
    findings: list[EvidenceBackedFinding] = Field(min_length=1)
    key_findings: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    primary_evidence_id: str | None = None

    @model_validator(mode="after")
    def synchronize_findings(self) -> "AutonomousSynthesisOutput":
        if self.findings and not self.key_findings:
            self.key_findings = [item.statement for item in self.findings]
        return self


class AutonomousBudget(BaseModel):
    max_iterations: int = Field(ge=1)
    max_tasks: int = Field(ge=1)
    max_parallel_tasks: int = Field(default=3, ge=1)
    max_queries: int = Field(ge=1)
    max_llm_tokens: int = Field(ge=1)
    max_active_execution_seconds: int = Field(ge=1)
    max_total_plan_cost: float = Field(default=500_000, ge=0)
    max_total_plan_rows: int = Field(default=1_000_000, ge=0)
    max_total_relation_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=0)
    max_total_database_seconds: float = Field(default=90.0, ge=0)
    default_task_budget: TaskBudget = Field(default_factory=TaskBudget)


class AutonomousBudgetUsage(BaseModel):
    iterations: int = 0
    tasks_created: int = 0
    queries_executed: int = 0
    llm_tokens: int = 0
    active_execution_seconds: float = 0.0
    total_plan_cost: float = 0.0
    total_plan_rows: int = 0
    total_relation_bytes: int = 0
    total_database_seconds: float = 0.0
    parallel_waves: int = 0
    cache_hits: int = 0
    exhausted_reasons: list[str] = Field(default_factory=list)


class AutonomousInvestigationSummary(BaseModel):
    enabled: bool = True
    mode: InvestigationMode | None = None
    routing_decision: AutonomousRoutingDecision | None = None
    plan: InvestigationPlan | None = None
    current_task_id: str | None = None
    proposals: list[SpecialistQueryProposal] = Field(default_factory=list)
    evidence: list[InvestigationEvidence] = Field(default_factory=list)
    findings: list[EvidenceBackedFinding] = Field(default_factory=list)
    trajectory: list[InvestigationTrajectoryEvent] = Field(default_factory=list)
    critic_review: CriticReviewOutput | None = None
    supervisor_decision: SupervisorDecision | None = None
    budget: AutonomousBudget | None = None
    budget_usage: AutonomousBudgetUsage = Field(default_factory=AutonomousBudgetUsage)


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
    failure_type: str | None = None
    error_code: str | None = None
    error_message: str | None = None
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
    scope_id: str | None = None
    specialist_id: str | None = None
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
    query_spec_ref: QuerySpecReference | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None
    autonomous_investigation: AutonomousInvestigationSummary | None = None


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
    autonomous_investigation: AutonomousInvestigationSummary | None = None
    export: ExcelExportAvailability | None = None
    run_version: int | None = None
    idempotent_replay: bool = False
    query_spec: SemanticQuerySpec | None = None
    compiled_sql_artifact: CompiledSqlArtifact | None = None
    sql_execution_state: str | None = None


class TeamsMessageRequest(BaseModel):
    channel_user_id: str
    display_name: str | None = None
    conversation_id: str
    text: str = Field(min_length=1, max_length=4000)


class TeamsMessageResponse(BaseModel):
    text: str
    awaiting_approval: bool = False
    run_id: UUID | None = None
