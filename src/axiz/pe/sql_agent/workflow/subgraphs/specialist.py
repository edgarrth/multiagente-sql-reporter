from __future__ import annotations

import time
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from axiz.pe.sql_agent.agents import DomainAnalystAgent, SqlEngineerAgent
from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    InvestigationQueryMode,
    InvestigationTask,
    SpecialistProposalReview,
    SpecialistQueryProposal,
    TaskBudgetUsage,
)
from axiz.pe.sql_agent.query_engines.base import QueryEngine
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm_usage import current_llm_usage_collector
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile
from axiz.pe.sql_agent.services.sql_artifacts import SqlArtifactService
from axiz.pe.sql_agent.skills.semantic_exploration import SemanticExplorationSkill
from axiz.pe.sql_agent.tools.proposal_governance import SpecialistProposalGovernance
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.tools.task_budget import TaskBudgetPolicy


class SpecialistSubgraphState(TypedDict, total=False):
    task: dict[str, Any]
    original_question: str
    conversation_memory: dict[str, Any]
    conversation_history: list[dict[str, str]]
    published_domains: list[dict[str, Any]]
    prior_evidence: list[dict[str, Any]]
    previous_sql: str
    proposal: dict[str, Any]


class SpecialistSubgraphFactory:
    """Build compact specialist subgraphs with open-ended SQL autonomy.

    The domain analyst refines the task, the SQL engineer receives the complete task and optional
    previous SQL, and deterministic gates validate the result. No fixed feedback-target schema is
    used in this subgraph.
    """

    def __init__(
        self,
        *,
        semantic_agent: SemanticExplorationSkill,
        sql_agent: SqlEngineerAgent,
        security_validator: SqlSecurityValidator,
        query_engine: QueryEngine,
        cache: AgentResponseCache,
        review_policy: ProposalReviewPolicy,
        conditional_review_enabled: bool = True,
        history_max_messages: int = 4,
        history_max_chars: int = 3200,
        prior_evidence_max_items: int = 4,
        prior_evidence_max_rows: int = 3,
        **_: Any,
    ) -> None:
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.security_validator = security_validator
        self.query_engine = query_engine
        self.cache = cache
        self.review_policy = review_policy
        self.conditional_review_enabled = conditional_review_enabled
        self.history_max_messages = max(0, history_max_messages)
        self.history_max_chars = max(0, history_max_chars)
        self.prior_evidence_max_items = max(0, prior_evidence_max_items)
        self.prior_evidence_max_rows = max(0, prior_evidence_max_rows)
        self.sql_artifacts = SqlArtifactService(dialect=query_engine.capabilities.dialect)

    @staticmethod
    def _memory_projection(memory: ConversationMemory) -> dict[str, Any]:
        return {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "last_domain": memory.last_domain,
            "last_sql": memory.last_sql,
            "last_sql_snapshot": (
                memory.last_sql_snapshot.model_dump(mode="json")
                if memory.last_sql_snapshot
                else None
            ),
            "last_source_objects": list(memory.last_source_objects),
            "pending_revision_feedback": memory.pending_revision_feedback,
        }

    def _bounded_history(self, history: list[dict[str, str]]) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        remaining = self.history_max_chars
        for item in history[-self.history_max_messages :]:
            if remaining <= 0:
                break
            content = str(item.get("content") or "")[:remaining]
            result.append({"role": str(item.get("role") or "unknown"), "content": content})
            remaining -= len(content)
        return result

    def _prior_evidence(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for item in evidence[-self.prior_evidence_max_items :]:
            result = dict(item.get("result") or {})
            projected.append(
                {
                    "question": item.get("question"),
                    "summary": item.get("summary"),
                    "findings": list(item.get("findings") or [])[:8],
                    "source_objects": list(item.get("source_objects") or []),
                    "result": {
                        "columns": list(result.get("columns") or []),
                        "rows": list(result.get("rows") or [])[: self.prior_evidence_max_rows],
                    },
                }
            )
        return projected

    def build(self, profile: SpecialistProfile, agent: DomainAnalystAgent):
        async def run(state: SpecialistSubgraphState) -> SpecialistSubgraphState:
            started = time.perf_counter()
            task = InvestigationTask.model_validate(state["task"])
            memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
            usage = TaskBudgetUsage.model_validate(task.task_budget_usage)
            error: str | None = None
            prepared = None
            generated = None
            context: dict[str, Any] = {}
            final_sql = ""
            artifact = None
            security: dict[str, Any] = {}
            cost: dict[str, Any] = {}
            review: SpecialistProposalReview | None = None

            try:
                prepared = await agent.prepare(
                    task=task,
                    original_question=state.get("original_question") or task.objective,
                    memory=memory,
                    published_domains=list(state.get("published_domains") or []),
                    prior_evidence=self._prior_evidence(list(state.get("prior_evidence") or [])),
                )
                if not prepared.can_proceed:
                    raise ValueError(prepared.block_reason or "The specialist cannot support the task")

                required_sources = (
                    list(memory.last_source_objects)
                    if task.query_mode == InvestigationQueryMode.REVISE_PREVIOUS
                    else []
                )
                context = await self.semantic_agent.explore(
                    prepared.refined_question,
                    prepared.domain,
                    compact=True,
                    catalog_focus=prepared.catalog_focus,
                    required_sources=required_sources,
                )

                previous_sql = (state.get("previous_sql") or memory.last_sql or "").strip()
                revision = task.query_mode == InvestigationQueryMode.REVISE_PREVIOUS and bool(previous_sql)
                last_review: dict[str, Any] = {}
                for _attempt in range(task.task_budget.max_attempts):
                    usage.attempts += 1
                    budget = TaskBudgetPolicy(task.task_budget).evaluate(usage)
                    if not budget.approved:
                        raise ValueError("Task budget exceeded: " + ", ".join(budget.violations))
                    generated = await self.sql_agent.generate(
                        question=prepared.refined_question,
                        semantic_context=context,
                        history=self._bounded_history(list(state.get("conversation_history") or [])),
                        structured_memory=self._memory_projection(memory),
                        feedback=task.objective if revision else None,
                        previous_sql=previous_sql if revision else None,
                        prior_review=last_review,
                    )
                    if generated.requires_clarification:
                        raise ValueError(
                            "CLARIFICATION_REQUIRED::"
                            + (generated.clarification_question or "Aclara la solicitud")
                        )
                    final_sql = generated.sql
                    artifact = self.sql_artifacts.compile(final_sql)
                    validation = self.security_validator.validate(
                        final_sql,
                        allowed_sources=list(context.get("allowed_sources") or []),
                        policy=dict(context.get("query_policy") or {}),
                        source_contracts=dict(context.get("source_contracts") or {}),
                    )
                    security = validation.model_dump(mode="json")
                    structural = list(artifact.validation.violations)
                    if validation.approved and not structural:
                        if validation.normalized_sql:
                            final_sql = validation.normalized_sql
                            artifact = self.sql_artifacts.compile(final_sql)
                        break
                    issues = [*validation.violations, *structural]
                    last_review = {
                        "failed_sql": final_sql,
                        "retry_instruction": (
                            "Corrige estos problemas sin alterar el objetivo: " + "; ".join(issues)
                        ),
                    }
                else:
                    raise ValueError("SQL generation exhausted its governed attempts")

                estimate = await self.query_engine.estimate_cost(
                    final_sql,
                    tables=list(security.get("tables") or []),
                )
                cost = estimate.model_dump(mode="json")

                generated_output = {
                    "interpretation": generated.interpretation if generated else "",
                    "assumptions": generated.assumptions if generated else [],
                    "change_summary": generated.change_summary if generated else [],
                    "source_objects": artifact.snapshot.sources if artifact else [],
                    "sql_snapshot": artifact.snapshot.model_dump(mode="json") if artifact else {},
                }
                decision = self.review_policy.evaluate(
                    task=task.model_dump(mode="json"),
                    generated_output=generated_output,
                    final_sql=final_sql,
                    semantic_context=context,
                    security_validation=security,
                    cost_validation=cost,
                )
                if self.conditional_review_enabled and decision.requires_llm_review:
                    review = await agent.review_proposal(
                        task=task,
                        prepared=prepared,
                        generated_output=generated_output,
                        final_sql=final_sql,
                        semantic_context=context,
                        security_validation=security,
                        cost_validation=cost,
                    )
                    review.review_mode = "llm"
                    review.review_reasons = decision.reasons
                else:
                    review = SpecialistProposalReview(
                        approved=True,
                        review_mode="deterministic",
                        review_reasons=decision.reasons,
                    )
            except Exception as exc:
                error = str(exc)

            collector = current_llm_usage_collector()
            if collector is not None:
                usage.llm_tokens = collector.tokens_for_scope(task.task_id)
            usage.active_seconds += max(0.0, time.perf_counter() - started)
            budget_decision = TaskBudgetPolicy(task.task_budget).evaluate(usage)
            gate = SpecialistProposalGovernance.evaluate(
                error=error,
                cache_hit=False,
                security_validation=security,
                cost_validation=cost,
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
                question=prepared.refined_question if prepared else task.objective,
                domain=prepared.domain if prepared else task.domain,
                interpretation=generated.interpretation if generated else "",
                sql=final_sql,
                assumptions=generated.assumptions if generated else [],
                source_objects=artifact.snapshot.sources if artifact else [],
                semantic_context=context,
                security_validation=security,
                cost_validation=cost,
                review=review,
                task_budget=task.task_budget,
                task_budget_usage=budget_decision.usage,
                cache_hit=False,
                block_reason=gate.block_reason,
                sql_snapshot=artifact.snapshot if artifact else None,
                compiled_sql_artifact=artifact,
            )
            return {"proposal": proposal.model_dump(mode="json")}

        graph = StateGraph(SpecialistSubgraphState)
        graph.add_node("run", run)
        graph.add_edge(START, "run")
        graph.add_edge("run", END)
        return graph.compile()
