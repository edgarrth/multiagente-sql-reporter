from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    ConversationMemory,
    InvestigationTask,
    SpecialistProposalReview,
    SpecialistTaskOutput,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM
from axiz.pe.sql_agent.services.specialist_registry import SpecialistProfile


class DomainAnalysisSkill:
    """Specialist reasoning for task refinement and risk-based proposal review.

    The surrounding LangGraph subgraph supplies tools and deterministic gates. This class never
    executes SQL or changes authority.
    """

    def __init__(self, profile: SpecialistProfile, llm: StructuredLLM) -> None:
        self.profile = profile
        self.llm = llm

    @staticmethod
    def _memory_projection(memory: ConversationMemory) -> dict[str, Any]:
        return {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "domain": memory.last_domain,
            "last_sql": memory.last_sql,
            "last_sql_snapshot": (
                memory.last_sql_snapshot.model_dump(mode="json")
                if memory.last_sql_snapshot
                else None
            ),
            "sources": list(memory.last_source_objects),
            "has_previous_sql": bool(memory.last_sql),
        }

    @staticmethod
    def _task_review_projection(task: InvestigationTask) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "objective": task.objective,
            "specialist": str(task.specialist),
            "domain": task.domain,
            "expected_evidence": list(task.expected_evidence)[:12],
            "query_mode": task.query_mode.value,
        }

    @staticmethod
    def _prepared_review_projection(prepared: SpecialistTaskOutput) -> dict[str, Any]:
        return {
            "task_id": prepared.task_id,
            "specialist": str(prepared.specialist),
            "refined_question": prepared.refined_question,
            "domain": prepared.domain,
            "expected_evidence": list(prepared.expected_evidence)[:12],
            "query_mode": prepared.query_mode.value,
            "catalog_focus": list(prepared.catalog_focus)[:12],
            "assumptions": list(prepared.assumptions)[:8],
        }

    @staticmethod
    def _generated_review_projection(generated_output: dict[str, Any]) -> dict[str, Any]:
        return {
            key: generated_output.get(key)
            for key in (
                "interpretation",
                "assumptions",
                "change_summary",
                "source_objects",
                "sql_snapshot",
            )
            if generated_output.get(key) not in (None, [], {}, "")
        }

    @staticmethod
    def _security_review_projection(validation: dict[str, Any]) -> dict[str, Any]:
        return {
            "approved": bool(validation.get("approved")),
            "statement_type": validation.get("statement_type"),
            "tables": list(validation.get("tables") or [])[:20],
            "columns": list(validation.get("columns") or [])[:40],
            "enforced_limit": validation.get("enforced_limit"),
            "violations": list(validation.get("violations") or [])[:12],
        }

    @staticmethod
    def _cost_review_projection(validation: dict[str, Any]) -> dict[str, Any]:
        """Keep planner summaries and deliberately exclude the potentially huge EXPLAIN tree."""
        return {
            key: validation.get(key)
            for key in (
                "approved",
                "engine",
                "dialect",
                "total_cost",
                "plan_rows",
                "max_node_rows",
                "plan_node_count",
                "relation_bytes",
                "max_plan_cost",
                "max_plan_rows",
                "max_relation_bytes",
                "timeout_seconds",
            )
            if validation.get(key) is not None
        } | {
            "warnings": list(validation.get("warnings") or [])[:12],
            "tables": list(validation.get("tables") or [])[:20],
            "plan_relations": list(validation.get("plan_relations") or [])[:20],
        }

    @classmethod
    def build_review_payload(
        cls,
        *,
        task: InvestigationTask,
        prepared: SpecialistTaskOutput,
        generated_output: dict[str, Any],
        final_sql: str,
        semantic_context: dict[str, Any],
        security_validation: dict[str, Any],
        cost_validation: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a bounded semantic-review payload without raw query-plan duplication."""
        return {
            "task": cls._task_review_projection(task),
            "prepared_task": cls._prepared_review_projection(prepared),
            "generated_output": cls._generated_review_projection(generated_output),
            "final_sql": final_sql[:12_000],
            "semantic_context": semantic_context,
            "security_validation": cls._security_review_projection(security_validation),
            "cost_validation": cls._cost_review_projection(cost_validation),
        }

    async def prepare(
        self,
        *,
        task: InvestigationTask,
        original_question: str,
        memory: ConversationMemory,
        published_domains: list[dict],
        prior_evidence: list[dict],
    ) -> SpecialistTaskOutput:
        system = f"""
You are {self.profile.display_name}, a specialist in a governed analytical society.
{self.profile.instructions}
Refine the delegated objective into one standalone analytical question. Preserve query_mode
exactly. Select only a published semantic domain allowed by your profile. Return a short
catalog_focus containing the concepts that retrieval should prioritize. Describe the evidence
needed, not SQL. You cannot execute tools, change permissions, approve security, skip HITL or
expand budgets. If the catalog cannot support the task, return can_proceed=false with a precise
block_reason. Preserve the user's language and do not expose hidden reasoning.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                {
                    "task": task.model_dump(mode="json"),
                    "original_question": original_question,
                    "memory": self._memory_projection(memory),
                    "profile": {
                        "role": self.profile.role,
                        "display_name": self.profile.display_name,
                        "description": self.profile.description,
                        "domains": self.profile.domains,
                        "capabilities": self.profile.capabilities,
                    },
                    "published_domains": published_domains,
                    "prior_evidence": prior_evidence,
                },
                ensure_ascii=False,
                default=str,
            ),
            response_model=SpecialistTaskOutput,
        )

    async def review_proposal(
        self,
        *,
        task: InvestigationTask,
        prepared: SpecialistTaskOutput,
        generated_output: dict,
        final_sql: str,
        semantic_context: dict,
        security_validation: dict,
        cost_validation: dict,
    ) -> SpecialistProposalReview:
        system = f"""
You are the risk-based self-review stage of {self.profile.display_name}. Evaluate whether the
proposed SQL and its generated interpretation answer the delegated task using only the compact published
semantic context. This call is made only when deterministic risk routing found a reason for an
additional semantic review. You cannot approve permissions, SQL security, query cost, HITL,
budgets or execution; those are immutable gates. Reject unsupported semantics, unrelated scope
changes or insufficient evidence. Return a concise retry_instruction when repair is possible. Do
not expose hidden reasoning.
""".strip()
        return await self.llm.parse(
            system=system,
            user=json.dumps(
                self.build_review_payload(
                    task=task,
                    prepared=prepared,
                    generated_output=generated_output,
                    final_sql=final_sql,
                    semantic_context=semantic_context,
                    security_validation=security_validation,
                    cost_validation=cost_validation,
                ),
                ensure_ascii=False,
                default=str,
            ),
            response_model=SpecialistProposalReview,
        )
