from __future__ import annotations

import time
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.agents.autonomous.domain_specialist_agent import DomainSpecialistAgent
from axiz.pe.sql_agent.agents.feedback_interpreter_agent import FeedbackInterpreterAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    CostValidation,
    InvestigationQueryMode,
    InvestigationTask,
    SecurityValidation,
    SpecialistProposalReview,
    SpecialistProposalStatus,
    SpecialistQueryProposal,
    SpecialistTaskOutput,
    SqlFeedbackPlan,
    SqlGenerationOutput,
    TaskBudgetUsage,
)
from axiz.pe.sql_agent.query_engines.base import QueryEngine
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm_usage import current_llm_usage_collector
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile
from axiz.pe.sql_agent.tools.proposal_governance import SpecialistProposalGovernance
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.tools.task_budget import TaskBudgetPolicy


class SpecialistSubgraphState(TypedDict, total=False):
    task: dict[str, Any]
    profile: dict[str, Any]
    original_question: str
    conversation_memory: dict[str, Any]
    conversation_history: list[dict[str, str]]
    published_domains: list[dict[str, Any]]
    prior_evidence: list[dict[str, Any]]
    previous_sql: str
    catalog_fingerprint: str
    model_fingerprint: str
    semantic_context: dict[str, Any]
    prepared_task: dict[str, Any]
    feedback_plan: dict[str, Any]
    generated_contract: dict[str, Any]
    final_sql: str
    security_validation: dict[str, Any]
    cost_validation: dict[str, Any]
    proposal_review: dict[str, Any]
    review_decision: dict[str, Any]
    proposal: dict[str, Any]
    task_usage: dict[str, Any]
    cache_hit: bool
    cache_key: str
    retry_instruction: str
    started_at: float
    error: str


class SpecialistSubgraphFactory:
    """Build one isolated, bounded specialist subgraph per profile.

    The specialist may refine a task and repair SQL, but deterministic parent and subgraph gates
    retain authority over budgets, security, cost, HITL and execution. Context is projected per
    stage so parallel specialists do not repeatedly process the complete catalog or conversation.
    """

    def __init__(
        self,
        *,
        semantic_agent: SemanticExplorerAgent,
        sql_agent: SqlGeneratorAgent,
        feedback_interpreter: FeedbackInterpreterAgent,
        feedback_applier: SqlFeedbackApplier,
        security_validator: SqlSecurityValidator,
        query_engine: QueryEngine,
        cache: AgentResponseCache,
        review_policy: ProposalReviewPolicy,
        conditional_review_enabled: bool = True,
        history_max_messages: int = 4,
        history_max_chars: int = 3200,
        prior_evidence_max_items: int = 4,
        prior_evidence_max_rows: int = 3,
    ) -> None:
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.feedback_interpreter = feedback_interpreter
        self.feedback_applier = feedback_applier
        self.security_validator = security_validator
        self.query_engine = query_engine
        self.cache = cache
        self.review_policy = review_policy
        self.conditional_review_enabled = conditional_review_enabled
        self.history_max_messages = max(0, history_max_messages)
        self.history_max_chars = max(0, history_max_chars)
        self.prior_evidence_max_items = max(0, prior_evidence_max_items)
        self.prior_evidence_max_rows = max(0, prior_evidence_max_rows)

    @staticmethod
    def _memory_projection(memory: ConversationMemory) -> dict[str, Any]:
        return {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "last_domain": memory.last_domain,
            "last_metrics": list(memory.last_metrics),
            "last_dimensions": list(memory.last_dimensions),
            "last_filters": [item.model_dump(mode="json") for item in memory.last_filters],
            "last_time_window": (
                memory.last_time_window.model_dump(mode="json")
                if memory.last_time_window
                else None
            ),
            "last_ordering": list(memory.last_ordering),
            "last_limit": memory.last_limit,
            "last_source_objects": list(memory.last_source_objects),
            "last_sql": memory.last_sql,
        }

    def _bounded_history(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        if not self.history_max_messages or not self.history_max_chars:
            return []
        result: list[dict[str, str]] = []
        consumed = 0
        for item in history[-self.history_max_messages :]:
            remaining = self.history_max_chars - consumed
            if remaining <= 0:
                break
            content = str(item.get("content") or "")[:remaining]
            result.append(
                {
                    "role": str(item.get("role") or "unknown")[:32],
                    "content": content,
                }
            )
            consumed += len(content)
        return result

    def _prior_evidence_projection(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for item in evidence[-self.prior_evidence_max_items :]:
            result = dict(item.get("result") or {})
            result["rows"] = list(result.get("rows") or [])[: self.prior_evidence_max_rows]
            projected.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "task_id": item.get("task_id"),
                    "specialist": item.get("specialist"),
                    "question": item.get("question"),
                    "summary": item.get("summary"),
                    "findings": list(item.get("findings") or [])[:8],
                    "caveats": list(item.get("caveats") or [])[:6],
                    "source_objects": list(item.get("source_objects") or []),
                    "result": {
                        "columns": result.get("columns") or [],
                        "rows": result.get("rows") or [],
                        "row_count": result.get("row_count"),
                        "truncated": result.get("truncated"),
                    },
                }
            )
        return projected

    def build(self, profile: SpecialistProfile, agent: DomainSpecialistAgent):
        async def initialize(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            return {
                "profile": profile.model_dump(mode="json"),
                "task_usage": task.task_budget_usage.model_dump(mode="json"),
                "cache_hit": False,
                "started_at": time.perf_counter(),
            }

        def cache_projection(state: SpecialistSubgraphState) -> dict[str, Any]:
            task = InvestigationTask.model_validate(state["task"])
            memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
            return {
                "contract_version": "specialist-proposal-v6",
                "specialist": profile.role,
                "task": task.model_dump(mode="json"),
                "original_question": state.get("original_question"),
                "previous_sql": state.get("previous_sql") or "",
                "memory": self._memory_projection(memory),
                "catalog_fingerprint": state.get("catalog_fingerprint"),
                "model_fingerprint": state.get("model_fingerprint"),
                "conditional_review_enabled": self.conditional_review_enabled,
            }

        async def lookup_cache(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            projection = cache_projection(state)
            fallback_key = self.cache.key("specialist-proposal", projection)
            lookup = await self.cache.get("specialist-proposal", projection)
            # Cache backends are non-critical. Be defensive against a malformed adapter or test
            # double returning None and continue as a cache miss rather than failing the run.
            if lookup is None:
                return {"cache_hit": False, "cache_key": fallback_key}
            lookup_key = getattr(lookup, "key", fallback_key)
            lookup_value = getattr(lookup, "value", None)
            if not bool(getattr(lookup, "hit", False)) or not lookup_value:
                return {"cache_hit": False, "cache_key": lookup_key}
            value = dict(lookup_value)
            return {
                "cache_hit": True,
                "cache_key": lookup_key,
                "prepared_task": value.get("prepared_task") or {},
                "feedback_plan": value.get("feedback_plan") or {},
                "generated_contract": value.get("generated_contract") or {},
                "final_sql": str(value.get("final_sql") or ""),
                "semantic_context": value.get("semantic_context") or {},
            }

        async def hydrate_prepared_task(
            state: SpecialistSubgraphState,
        ) -> SpecialistSubgraphState:
            """Reuse the adaptive router's bounded task refinement for direct mode."""
            task = InvestigationTask.model_validate(state["task"])
            if not task.specialist_question:
                return {"error": "Direct specialist task is missing a refined question"}
            prepared = SpecialistTaskOutput(
                task_id=task.task_id,
                specialist=task.specialist,
                refined_question=task.specialist_question,
                domain=task.domain,
                expected_evidence=task.expected_evidence,
                query_mode=task.query_mode,
                catalog_focus=task.expected_evidence,
                can_proceed=True,
            )
            return {"prepared_task": prepared.model_dump(mode="json")}

        async def prepare_task(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            decision = TaskBudgetPolicy(task.task_budget).evaluate(usage)
            if not decision.approved:
                return {
                    "task_usage": decision.usage.model_dump(mode="json"),
                    "error": "Task budget exceeded before specialist preparation: "
                    + ", ".join(decision.violations),
                }
            output = await agent.prepare(
                task=task,
                original_question=state["original_question"],
                memory=ConversationMemory.model_validate(state.get("conversation_memory") or {}),
                published_domains=list(state.get("published_domains") or []),
                prior_evidence=self._prior_evidence_projection(
                    list(state.get("prior_evidence") or [])
                ),
            )
            return {
                "prepared_task": output.model_dump(mode="json"),
                "task_usage": decision.usage.model_dump(mode="json"),
            }

        async def explore_semantics(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            prepared = SpecialistTaskOutput.model_validate(state["prepared_task"])
            if not prepared.can_proceed or not prepared.domain:
                return {"error": prepared.block_reason or "Specialist cannot proceed"}
            context = await self.semantic_agent.explore(
                prepared.refined_question,
                prepared.domain,
                compact=True,
                catalog_focus=prepared.catalog_focus,
            )
            return {"semantic_context": context}

        async def interpret_revision(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            if task.query_mode != InvestigationQueryMode.REVISE_PREVIOUS:
                return {"feedback_plan": {}}
            previous_sql = (state.get("previous_sql") or "").strip()
            if not previous_sql:
                return {"error": "A revision task requires an approved previous SQL"}
            memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
            plan = await self.feedback_interpreter.interpret(
                feedback=task.objective,
                previous_sql=previous_sql,
                semantic_context=state["semantic_context"],
                current_contract={
                    "interpretation": memory.last_interpretation,
                    "metrics": memory.last_metrics,
                    "dimensions": memory.last_dimensions,
                    "filters": [item.model_dump(mode="json") for item in memory.last_filters],
                    "time_window": (
                        memory.last_time_window.model_dump(mode="json")
                        if memory.last_time_window
                        else None
                    ),
                    "ordering": memory.last_ordering,
                    "limit": memory.last_limit,
                    "sources": memory.last_source_objects,
                },
            )
            if plan.requires_clarification:
                return {"error": plan.clarification_question or "Revision is ambiguous"}
            return {"feedback_plan": plan.model_dump(mode="json")}

        async def generate_sql(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            collector = current_llm_usage_collector()
            if collector is not None:
                usage.llm_tokens = collector.tokens_for_scope(task.task_id)
            usage.attempts += 1
            budget_decision = TaskBudgetPolicy(task.task_budget).evaluate(usage)
            if not budget_decision.approved:
                return {
                    "task_usage": budget_decision.usage.model_dump(mode="json"),
                    "error": "Task budget exceeded before SQL generation: "
                    + ", ".join(budget_decision.violations),
                }
            prepared = SpecialistTaskOutput.model_validate(state["prepared_task"])
            feedback_plan = dict(state.get("feedback_plan") or {})
            memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
            generated = await self.sql_agent.generate(
                question=prepared.refined_question,
                semantic_context=state["semantic_context"],
                history=self._bounded_history(list(state.get("conversation_history") or [])),
                structured_memory=self._memory_projection(memory),
                feedback=(task.objective if feedback_plan else None),
                previous_sql=(state.get("previous_sql") or None),
                feedback_plan=feedback_plan or None,
                prior_compliance={
                    "retry_instruction": state.get("retry_instruction") or "",
                    "failed_sql": (
                        (state.get("final_sql") or "")
                        if state.get("retry_instruction")
                        else ""
                    ),
                },
            )
            final_sql = generated.sql
            if feedback_plan:
                application = self.feedback_applier.apply(
                    final_sql,
                    SqlFeedbackPlan.model_validate(feedback_plan),
                    previous_sql=state.get("previous_sql") or None,
                )
                final_sql = application.sql
            return {
                "generated_contract": generated.model_dump(mode="json"),
                "final_sql": final_sql,
                "task_usage": budget_decision.usage.model_dump(mode="json"),
                "cache_hit": False,
                "security_validation": {},
                "cost_validation": {},
                "proposal_review": {},
                "review_decision": {},
                "error": "",
            }

        async def validate_security(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            context = state["semantic_context"]
            validation = self.security_validator.validate(
                state["final_sql"],
                allowed_sources=context["allowed_sources"],
                policy=context["query_policy"],
            )
            if not validation.approved:
                return {
                    "security_validation": validation.model_dump(mode="json"),
                    "retry_instruction": "Repair all deterministic SQL security violations: "
                    + "; ".join(validation.violations),
                }
            return {
                "security_validation": validation.model_dump(mode="json"),
                "final_sql": validation.normalized_sql or state["final_sql"],
            }

        async def estimate_cost(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            security = SecurityValidation.model_validate(state["security_validation"])
            if not security.approved:
                return {}
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            cost = await self.query_engine.estimate_cost(state["final_sql"], security.tables)
            # A task produces one executable SQL proposal. EXPLAIN/repair retries validate that
            # same proposal slot and must not consume a new query slot on every attempt.
            decision = TaskBudgetPolicy(task.task_budget).evaluate_query_proposal(
                usage,
                cost=cost,
            )
            if not cost.approved or not decision.approved:
                reasons = list(cost.warnings) + decision.violations
                if cost.failure_type == "sql_validation":
                    database_feedback = cost.error_message or "; ".join(cost.warnings)
                    retry_instruction = (
                        "The database rejected the previous SQL during EXPLAIN. Repair the SQL "
                        "without changing the requested business result. Use only exact column "
                        "names, source objects and categorical values present in semantic_context. "
                        "Do not repeat the rejected identifier or value. Database feedback: "
                        + database_feedback
                    )
                    if decision.violations:
                        retry_instruction += ". Budget findings: " + "; ".join(
                            decision.violations
                        )
                else:
                    retry_instruction = (
                        "Optimize the proposal without changing the requested semantics. "
                        "Budget/cost findings: " + "; ".join(reasons)
                    )
                return {
                    "cost_validation": cost.model_dump(mode="json"),
                    "task_usage": decision.usage.model_dump(mode="json"),
                    "retry_instruction": retry_instruction,
                }
            return {
                "cost_validation": cost.model_dump(mode="json"),
                "task_usage": decision.usage.model_dump(mode="json"),
            }

        async def self_review(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            security = SecurityValidation.model_validate(
                state.get("security_validation") or {"approved": False}
            )
            cost = CostValidation.model_validate(
                state.get("cost_validation") or {"approved": False}
            )
            if not security.approved or not cost.approved:
                return {}
            task = InvestigationTask.model_validate(state["task"])
            prepared = SpecialistTaskOutput.model_validate(state["prepared_task"])
            decision = self.review_policy.evaluate(
                task=task.model_dump(mode="json"),
                generated_contract=dict(state.get("generated_contract") or {}),
                final_sql=state.get("final_sql") or "",
                semantic_context=dict(state.get("semantic_context") or {}),
                security_validation=dict(state.get("security_validation") or {}),
                cost_validation=dict(state.get("cost_validation") or {}),
                feedback_plan=dict(state.get("feedback_plan") or {}),
            )
            if self.conditional_review_enabled and not decision.requires_llm_review:
                review = SpecialistProposalReview(
                    approved=True,
                    task_alignment=True,
                    catalog_alignment=True,
                    evidence_sufficient=True,
                    confidence=1.0,
                    review_mode="deterministic",
                    review_reasons=decision.checks,
                )
            else:
                review = await agent.review_proposal(
                    task=task,
                    prepared=prepared,
                    generated_contract=state["generated_contract"],
                    final_sql=state["final_sql"],
                    semantic_context=SemanticContextProjector.for_review(
                        state["semantic_context"]
                    ),
                    security_validation=state["security_validation"],
                    cost_validation=state["cost_validation"],
                )
                review = review.model_copy(
                    update={
                        "review_mode": "llm",
                        "review_reasons": decision.reasons,
                    }
                )
            return {
                "proposal_review": review.model_dump(mode="json"),
                "review_decision": decision.model_dump(mode="json"),
                "retry_instruction": review.retry_instruction or "",
            }

        async def finalize(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            prepared_payload = state.get("prepared_task") or {}
            generated_payload = state.get("generated_contract") or {}
            review_payload = state.get("proposal_review") or {}
            prepared = SpecialistTaskOutput.model_validate(prepared_payload) if prepared_payload else None
            generated = SqlGenerationOutput.model_validate(generated_payload) if generated_payload else None
            review = SpecialistProposalReview.model_validate(review_payload) if review_payload else None
            error = state.get("error")

            collector = current_llm_usage_collector()
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            if collector is not None:
                usage.llm_tokens = collector.tokens_for_scope(task.task_id)
            usage.active_seconds += max(
                0.0,
                time.perf_counter() - float(state.get("started_at") or time.perf_counter()),
            )
            budget_decision = TaskBudgetPolicy(task.task_budget).evaluate(usage)
            usage = budget_decision.usage
            gate = SpecialistProposalGovernance.evaluate(
                error=error,
                cache_hit=bool(state.get("cache_hit")),
                security_validation=dict(state.get("security_validation") or {}),
                cost_validation=dict(state.get("cost_validation") or {}),
                review=review,
                task_budget_approved=budget_decision.approved,
                task_budget_violations=budget_decision.violations,
            )
            proposal = SpecialistQueryProposal(
                proposal_id=f"proposal-{uuid4().hex[:12]}",
                task_id=task.task_id,
                specialist_id=profile.role,
                wave=task.wave,
                status=gate.status,
                question=prepared.refined_question if prepared is not None else task.objective,
                domain=prepared.domain if prepared is not None else task.domain,
                interpretation=generated.interpretation if generated is not None else "",
                sql=state.get("final_sql") or "",
                assumptions=generated.assumptions if generated is not None else [],
                selected_metrics=generated.selected_metrics if generated is not None else [],
                selected_dimensions=generated.selected_dimensions if generated is not None else [],
                selected_filters=generated.selected_filters if generated is not None else [],
                time_window=generated.time_window if generated is not None else None,
                source_objects=generated.source_objects if generated is not None else [],
                semantic_context=dict(state.get("semantic_context") or {}),
                security_validation=dict(state.get("security_validation") or {}),
                cost_validation=dict(state.get("cost_validation") or {}),
                review=review,
                task_budget=task.task_budget,
                task_budget_usage=usage,
                cache_hit=bool(state.get("cache_hit")),
                cache_key=state.get("cache_key"),
                block_reason=gate.block_reason,
            )
            if proposal.status == SpecialistProposalStatus.READY and not proposal.cache_hit:
                await self.cache.set(
                    "specialist-proposal",
                    cache_projection(state),
                    {
                        "prepared_task": prepared_payload,
                        "feedback_plan": state.get("feedback_plan") or {},
                        "generated_contract": generated_payload,
                        "final_sql": proposal.sql,
                        "semantic_context": proposal.semantic_context,
                    },
                    ttl_seconds=profile.cache_ttl_seconds,
                )
            return {"proposal": proposal.model_dump(mode="json")}

        def route_after_cache(state: SpecialistSubgraphState) -> str:
            if state.get("cache_hit"):
                return "validate_security"
            task = InvestigationTask.model_validate(state["task"])
            return "hydrate_prepared_task" if task.specialist_question else "prepare_task"

        def route_after_prepare(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "explore_semantics"

        def route_after_revision(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "generate_sql"

        def route_after_generation(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "validate_security"

        def route_after_security(state: SpecialistSubgraphState) -> str:
            if (state.get("security_validation") or {}).get("approved"):
                return "estimate_cost"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        def route_after_cost(state: SpecialistSubgraphState) -> str:
            if (state.get("cost_validation") or {}).get("approved"):
                return "self_review"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        def route_after_review(state: SpecialistSubgraphState) -> str:
            if (state.get("proposal_review") or {}).get("approved"):
                return "finalize"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        graph = StateGraph(SpecialistSubgraphState)
        for name, node in (
            ("initialize", initialize),
            ("lookup_cache", lookup_cache),
            ("hydrate_prepared_task", hydrate_prepared_task),
            ("prepare_task", prepare_task),
            ("explore_semantics", explore_semantics),
            ("interpret_revision", interpret_revision),
            ("generate_sql", generate_sql),
            ("validate_security", validate_security),
            ("estimate_cost", estimate_cost),
            ("self_review", self_review),
            ("finalize", finalize),
        ):
            graph.add_node(name, node)
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "lookup_cache")
        graph.add_conditional_edges(
            "lookup_cache",
            route_after_cache,
            {
                "validate_security": "validate_security",
                "hydrate_prepared_task": "hydrate_prepared_task",
                "prepare_task": "prepare_task",
            },
        )
        graph.add_edge("hydrate_prepared_task", "explore_semantics")
        graph.add_conditional_edges(
            "prepare_task",
            route_after_prepare,
            {"explore_semantics": "explore_semantics", "finalize": "finalize"},
        )
        graph.add_edge("explore_semantics", "interpret_revision")
        graph.add_conditional_edges(
            "interpret_revision",
            route_after_revision,
            {"generate_sql": "generate_sql", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "generate_sql",
            route_after_generation,
            {"validate_security": "validate_security", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "validate_security",
            route_after_security,
            {
                "estimate_cost": "estimate_cost",
                "generate_sql": "generate_sql",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "estimate_cost",
            route_after_cost,
            {
                "self_review": "self_review",
                "generate_sql": "generate_sql",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "self_review",
            route_after_review,
            {"generate_sql": "generate_sql", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile()
