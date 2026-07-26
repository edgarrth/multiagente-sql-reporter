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
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.tools.task_budget import TaskBudgetPolicy
from axiz.pe.sql_agent.tools.proposal_governance import SpecialistProposalGovernance


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
    proposal: dict[str, Any]
    task_usage: dict[str, Any]
    cache_hit: bool
    cache_key: str
    retry_instruction: str
    started_at: float
    error: str


class SpecialistSubgraphFactory:
    """Build a per-profile, per-invocation LangGraph specialist subgraph.

    Autonomous decisions are limited to evidence preparation and SQL repair. Permissions,
    security, cost, budgets and execution remain deterministic. The compiled graph is reusable;
    each invocation receives isolated state from the parent graph.
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
    ) -> None:
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.feedback_interpreter = feedback_interpreter
        self.feedback_applier = feedback_applier
        self.security_validator = security_validator
        self.query_engine = query_engine
        self.cache = cache

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
            memory = ConversationMemory.model_validate(
                state.get("conversation_memory") or {}
            )
            return {
                "contract_version": "specialist-proposal-v2",
                "specialist": profile.role,
                "task": task.model_dump(mode="json"),
                "original_question": state.get("original_question"),
                "previous_sql": state.get("previous_sql") or "",
                "memory": {
                    "last_resolved_question": memory.last_resolved_question,
                    "last_interpretation": memory.last_interpretation,
                    "last_metrics": memory.last_metrics,
                    "last_dimensions": memory.last_dimensions,
                    "last_filters": [
                        item.model_dump(mode="json") for item in memory.last_filters
                    ],
                    "last_time_window": (
                        memory.last_time_window.model_dump(mode="json")
                        if memory.last_time_window
                        else None
                    ),
                    "last_ordering": memory.last_ordering,
                    "last_limit": memory.last_limit,
                    "last_source_objects": memory.last_source_objects,
                },
                "catalog_fingerprint": state.get("catalog_fingerprint"),
                "model_fingerprint": state.get("model_fingerprint"),
            }

        async def lookup_cache(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            lookup = await self.cache.get("specialist-proposal", cache_projection(state))
            if not lookup.hit or not lookup.value:
                return {"cache_hit": False, "cache_key": lookup.key}
            value = dict(lookup.value)
            return {
                "cache_hit": True,
                "cache_key": lookup.key,
                "prepared_task": value.get("prepared_task") or {},
                "feedback_plan": value.get("feedback_plan") or {},
                "generated_contract": value.get("generated_contract") or {},
                "final_sql": str(value.get("final_sql") or ""),
                "semantic_context": value.get("semantic_context") or {},
            }

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
                memory=ConversationMemory.model_validate(
                    state.get("conversation_memory") or {}
                ),
                published_domains=list(state.get("published_domains") or []),
                prior_evidence=list(state.get("prior_evidence") or []),
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
            )
            return {"semantic_context": context}

        async def interpret_revision(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            if task.query_mode != InvestigationQueryMode.REVISE_PREVIOUS:
                return {"feedback_plan": {}}
            previous_sql = (state.get("previous_sql") or "").strip()
            if not previous_sql:
                return {"error": "A revision task requires an approved previous SQL"}
            memory = ConversationMemory.model_validate(
                state.get("conversation_memory") or {}
            )
            plan = await self.feedback_interpreter.interpret(
                feedback=task.objective,
                previous_sql=previous_sql,
                semantic_context=state["semantic_context"],
                current_contract={
                    "interpretation": memory.last_interpretation,
                    "metrics": memory.last_metrics,
                    "dimensions": memory.last_dimensions,
                    "filters": [
                        item.model_dump(mode="json") for item in memory.last_filters
                    ],
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
            generated = await self.sql_agent.generate(
                question=prepared.refined_question,
                semantic_context=state["semantic_context"],
                history=list(state.get("conversation_history") or []),
                structured_memory=dict(state.get("conversation_memory") or {}),
                feedback=(task.objective if feedback_plan else None),
                previous_sql=(state.get("previous_sql") or None),
                feedback_plan=feedback_plan or None,
                prior_compliance={
                    "retry_instruction": state.get("retry_instruction") or ""
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
                # Any regeneration invalidates cache provenance. The resulting proposal can be
                # cached again only after all current deterministic gates pass.
                "cache_hit": False,
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
                    "retry_instruction": (
                        "Repair all deterministic SQL security violations: "
                        + "; ".join(validation.violations)
                    ),
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
            cost = await self.query_engine.estimate_cost(
                state["final_sql"],
                security.tables,
            )
            decision = TaskBudgetPolicy(task.task_budget).evaluate(
                usage,
                cost=cost,
                additional_queries=1,
            )
            if not cost.approved or not decision.approved:
                reasons = list(cost.warnings) + decision.violations
                return {
                    "cost_validation": cost.model_dump(mode="json"),
                    "task_usage": decision.usage.model_dump(mode="json"),
                    "retry_instruction": (
                        "Optimize the proposal without changing the requested semantics. "
                        "Budget/cost findings: " + "; ".join(reasons)
                    ),
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
            review = await agent.review_proposal(
                task=task,
                prepared=prepared,
                generated_contract=state["generated_contract"],
                final_sql=state["final_sql"],
                semantic_context=state["semantic_context"],
                security_validation=state["security_validation"],
                cost_validation=state["cost_validation"],
            )
            return {
                "proposal_review": review.model_dump(mode="json"),
                "retry_instruction": review.retry_instruction or "",
            }

        async def finalize(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            task = InvestigationTask.model_validate(state["task"])
            prepared_payload = state.get("prepared_task") or {}
            generated_payload = state.get("generated_contract") or {}
            review_payload = state.get("proposal_review") or {}
            prepared = (
                SpecialistTaskOutput.model_validate(prepared_payload)
                if prepared_payload
                else None
            )
            generated = (
                SqlGenerationOutput.model_validate(generated_payload)
                if generated_payload
                else None
            )
            review = (
                SpecialistProposalReview.model_validate(review_payload)
                if review_payload
                else None
            )
            error = state.get("error")

            collector = current_llm_usage_collector()
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            if collector is not None:
                usage.llm_tokens = collector.tokens_for_scope(task.task_id)
            usage.active_seconds += max(
                0.0, time.perf_counter() - float(state.get("started_at") or time.perf_counter())
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
            status = gate.status
            error = gate.block_reason

            proposal = SpecialistQueryProposal(
                proposal_id=f"proposal-{uuid4().hex[:12]}",
                task_id=task.task_id,
                specialist_id=profile.role,
                wave=task.wave,
                status=status,
                question=(
                    prepared.refined_question
                    if prepared is not None
                    else task.objective
                ),
                domain=(prepared.domain if prepared is not None else task.domain),
                interpretation=(generated.interpretation if generated is not None else ""),
                sql=state.get("final_sql") or "",
                assumptions=(generated.assumptions if generated is not None else []),
                selected_metrics=(
                    generated.selected_metrics if generated is not None else []
                ),
                selected_dimensions=(
                    generated.selected_dimensions if generated is not None else []
                ),
                selected_filters=(
                    generated.selected_filters if generated is not None else []
                ),
                time_window=(generated.time_window if generated is not None else None),
                source_objects=(generated.source_objects if generated is not None else []),
                semantic_context=dict(state.get("semantic_context") or {}),
                security_validation=dict(state.get("security_validation") or {}),
                cost_validation=dict(state.get("cost_validation") or {}),
                review=review,
                task_budget=task.task_budget,
                task_budget_usage=usage,
                cache_hit=bool(state.get("cache_hit")),
                cache_key=state.get("cache_key"),
                block_reason=error,
            )
            if (
                proposal.status == SpecialistProposalStatus.READY
                and not proposal.cache_hit
            ):
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
            return "validate_security" if state.get("cache_hit") else "prepare_task"

        def route_after_prepare(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "explore_semantics"

        def route_after_revision(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "generate_sql"

        def route_after_security(state: SpecialistSubgraphState) -> str:
            security = state.get("security_validation") or {}
            if security.get("approved"):
                return "estimate_cost"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        def route_after_cost(state: SpecialistSubgraphState) -> str:
            cost = state.get("cost_validation") or {}
            if cost.get("approved"):
                return "self_review"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        def route_after_review(state: SpecialistSubgraphState) -> str:
            review = state.get("proposal_review") or {}
            if review.get("approved"):
                return "finalize"
            task = InvestigationTask.model_validate(state["task"])
            usage = TaskBudgetUsage.model_validate(state.get("task_usage") or {})
            return "generate_sql" if usage.attempts < task.task_budget.max_attempts else "finalize"

        graph = StateGraph(SpecialistSubgraphState)
        graph.add_node("initialize", initialize)
        graph.add_node("lookup_cache", lookup_cache)
        graph.add_node("prepare_task", prepare_task)
        graph.add_node("explore_semantics", explore_semantics)
        graph.add_node("interpret_revision", interpret_revision)
        graph.add_node("generate_sql", generate_sql)
        graph.add_node("validate_security", validate_security)
        graph.add_node("estimate_cost", estimate_cost)
        graph.add_node("self_review", self_review)
        graph.add_node("finalize", finalize)
        graph.add_edge(START, "initialize")
        graph.add_edge("initialize", "lookup_cache")
        graph.add_conditional_edges(
            "lookup_cache",
            route_after_cache,
            {"validate_security": "validate_security", "prepare_task": "prepare_task"},
        )
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
        def route_after_generation(state: SpecialistSubgraphState) -> str:
            return "finalize" if state.get("error") else "validate_security"

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
