from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    ContextRelation,
    ContextResolutionOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.llm import StructuredLLM


class ContextResolutionSkill:
    """Classifies message dependency and resolves analytical follow-ups semantically.

    The resolver deliberately avoids domain-word and phrase heuristics. A structured LLM
    classifies whether the message is independent, changes a previous analytical request,
    references the session, or is ambiguous. Only analytical follow-ups inherit memory.
    """

    _CONFIDENCE_THRESHOLD = 0.72
    _MAX_HISTORY_MESSAGES = 6
    _MAX_HISTORY_CHARS_PER_MESSAGE = 1200
    _MAX_HISTORY_TOTAL_CHARS = 4800

    def __init__(self, llm: StructuredLLM, cache: AgentResponseCache | None = None) -> None:
        self.llm = llm
        self.cache = cache


    def _model_profile_projection(self) -> dict[str, Any]:
        registry = getattr(self.llm, "registry", None)
        agent_name = getattr(self.llm, "agent_name", self.llm.__class__.__name__)
        if registry is None or not hasattr(registry, "profile_for"):
            return {"agent": agent_name, "adapter": self.llm.__class__.__name__}
        return registry.profile_for(agent_name).model_dump(mode="json")

    async def resolve(
        self,
        *,
        question: str,
        memory: ConversationMemory,
        history: list[dict[str, str]],
    ) -> ContextResolutionOutput:
        normalized = " ".join(question.strip().split())
        if not normalized:
            return self._clarification(
                question,
                relation=ContextRelation.AMBIGUOUS,
                message="Escribe la solicitud analítica que deseas realizar.",
                rationale="The current message is empty after normalization.",
            )

        # With no approved analytical state and no earlier user request in the visible history,
        # there is nothing to resolve as a follow-up. Route the message as a new request and let the
        # catalog-driven analyst determine its semantics. This prevents the context layer from
        # inventing mandatory parameters such as a date range for otherwise complete top-N queries.
        prior_user_messages = [
            " ".join(str(item.get("content") or "").strip().split())
            for item in history
            if str(item.get("role") or "").lower() == "user"
            and " ".join(str(item.get("content") or "").strip().split())
            and " ".join(str(item.get("content") or "").strip().split()) != normalized
        ]
        if (
            not memory.last_resolved_question
            and not memory.last_sql
            and not prior_user_messages
        ):
            return ContextResolutionOutput(
                original_question=question,
                resolved_question=question,
                relation=ContextRelation.INDEPENDENT_REQUEST,
                confidence=1.0,
                rationale="No prior analytical state exists; route as a fresh catalog-driven request.",
            )

        payload = {
            "current_message": normalized,
            "has_previous_analytical_request": bool(memory.last_resolved_question),
            "has_previous_approved_sql": bool(memory.last_sql),
            "structured_memory": self._memory_projection(memory),
            "recent_conversation": self._bounded_history(history),
        }
        cache_payload = {
            "contract_version": "context-resolution-v5",
            "payload": payload,
            "agent": getattr(self.llm, "agent_name", self.llm.__class__.__name__),
            "model_profile": self._model_profile_projection(),
        }
        if self.cache is not None:
            cached = await self.cache.get("context-resolution", cache_payload)
            if cached.hit and cached.value:
                try:
                    output = ContextResolutionOutput.model_validate(cached.value)
                    output = output.model_copy(update={"original_question": question})
                    return self._enforce_policy(output, question, memory)
                except Exception:
                    pass

        system = """
You are the semantic context resolver for a governed analytics assistant.
Classify the relationship of the current message to the previous analytical state.

Use exactly one relation:
- independent_request: the message can be understood without inheriting any previous analytical
  metric, dimension, filter, period, source, ordering, limit, SQL, or result. This includes new
  analytical questions, catalog questions, capability questions, and unrelated requests.
- analytical_follow_up: the message changes, extends, narrows, compares, reformats, or reuses a
  previous analytical request and cannot be interpreted faithfully without that prior state.
- session_reference: the message asks about what was previously requested, generated, executed,
  returned, approved, modeled, or consumed; it does not request a new SQL proposal.
- ambiguous: the dependency cannot be determined safely.

Decide from the complete semantic meaning, never from isolated lexical cues, entity names,
temporal expressions, grammatical forms, or domain vocabulary. A message that contains enough
information to define its own objective and scope is independent, regardless of how it is worded.

For independent_request and session_reference, preserve the current message verbatim as
resolved_question and do not inherit fields.
For analytical_follow_up, always rewrite the request as one standalone analytical question,
preserving the newest instruction and inheriting only necessary fields. If approved SQL exists,
mark it as a SQL revision. If approved SQL does not exist but recent conversation contains enough
information to reconstruct the objective, keep analytical_follow_up but set requires_sql_revision=false
so the request is generated from the catalog as a fresh proposal. Ask for clarification only when
neither structured memory nor recent conversation can resolve the missing business meaning.
Never invent metrics, dimensions, filters, dates, sources, formulas, ordering, limits, or business
definitions. A top-N/latest request is complete when it identifies the entity, ordering meaning and
row count; it does not require an explicit date range. For example, "las 20 últimas transacciones"
means order by the catalog's published recency field descending and limit 20.
For ambiguous, require one concise clarification question.
Do not generate SQL. Structured memory is authoritative for approved state; recent conversation may
be used to recover a failed or unapproved request but must not override approved state. Answer in the
user's language.
""".strip()

        try:
            output = await self.llm.parse(
                system=system,
                user=json.dumps(payload, ensure_ascii=False, default=str),
                response_model=ContextResolutionOutput,
            )
        except Exception:
            return self._safe_fallback(question, memory)

        output = output.model_copy(update={"original_question": question})
        governed = self._enforce_policy(output, question, memory)
        if self.cache is not None and not governed.requires_clarification:
            await self.cache.set(
                "context-resolution",
                cache_payload,
                governed.model_dump(mode="json"),
                ttl_seconds=600,
            )
        return governed

    def _enforce_policy(
        self,
        output: ContextResolutionOutput,
        question: str,
        memory: ConversationMemory,
    ) -> ContextResolutionOutput:
        if output.confidence < self._CONFIDENCE_THRESHOLD:
            return self._clarification(
                question,
                relation=ContextRelation.AMBIGUOUS,
                message=(
                    output.clarification_question
                    or "No pude determinar si deseas una consulta nueva o modificar la anterior. "
                    "Indica cuál de las dos opciones corresponde."
                ),
                rationale=output.rationale or "Context relation confidence is below threshold.",
                confidence=output.confidence,
            )

        if output.relation == ContextRelation.ANALYTICAL_FOLLOW_UP:
            if not output.resolved_question.strip():
                return self._clarification(
                    question,
                    relation=ContextRelation.AMBIGUOUS,
                    message="No pude construir una solicitud autocontenida. Reformula el cambio.",
                    rationale="The resolver returned an empty standalone question.",
                    confidence=output.confidence,
                )
            has_approved_sql = bool(memory.last_sql)
            # Follow-ups to a failed or unapproved attempt remain usable. They are routed through
            # ordinary catalog-driven generation instead of being rejected merely because no SQL
            # was persisted yet.
            return output.model_copy(
                update={
                    "relation": ContextRelation.ANALYTICAL_FOLLOW_UP,
                    "is_follow_up": True,
                    "requires_sql_revision": has_approved_sql,
                    "requires_clarification": False,
                    "clarification_question": None,
                }
            )

        if output.relation == ContextRelation.AMBIGUOUS:
            return self._clarification(
                question,
                relation=ContextRelation.AMBIGUOUS,
                message=(
                    output.clarification_question
                    or "Aclara si deseas una consulta nueva o modificar la consulta anterior."
                ),
                rationale=output.rationale or "The request is semantically ambiguous.",
                confidence=output.confidence,
            )

        return output.model_copy(
            update={
                "resolved_question": question,
                "is_follow_up": False,
                "requires_sql_revision": False,
                "inherited_fields": [],
                "requires_clarification": False,
                "clarification_question": None,
            }
        )

    def _safe_fallback(
        self, question: str, memory: ConversationMemory
    ) -> ContextResolutionOutput:
        # Without prior state there is nothing safe to inherit; let the ordinary intent/domain
        # router evaluate the message. With prior state, avoid silently treating a possible delta
        # as an independent request when the resolver is unavailable.
        if not memory.last_resolved_question:
            return ContextResolutionOutput(
                original_question=question,
                resolved_question=question,
                relation=ContextRelation.INDEPENDENT_REQUEST,
                confidence=0.70,
                rationale="Context model unavailable; no prior analytical state exists.",
            )
        return self._clarification(
            question,
            relation=ContextRelation.AMBIGUOUS,
            message=(
                "No pude determinar de forma segura si deseas una consulta nueva o modificar "
                "la anterior. Reformula la solicitud indicando esa intención."
            ),
            rationale="Context model unavailable while prior analytical state exists.",
            confidence=0.0,
        )

    @staticmethod
    def _memory_projection(memory: ConversationMemory) -> dict[str, Any]:
        return {
            "last_resolved_question": memory.last_resolved_question,
            "last_interpretation": memory.last_interpretation,
            "domain": memory.last_domain,
            "metrics": list(memory.last_metrics),
            "dimensions": list(memory.last_dimensions),
            "filters": [item.model_dump(mode="json") for item in memory.last_filters],
            "time_window": (
                memory.last_time_window.model_dump(mode="json")
                if memory.last_time_window
                else None
            ),
            "ordering": list(memory.last_ordering),
            "limit": memory.last_limit,
            "sources": list(memory.last_source_objects),
            "last_sql": memory.last_sql,
            "pending_revision_feedback": memory.pending_revision_feedback,
            "pending_revision_plan": dict(memory.pending_revision_plan),
            "last_attempt_status": memory.last_attempt_status,
            "last_attempt_error": memory.last_attempt_error,
            "last_result_schema": list(memory.last_result_schema),
            "last_row_count": memory.last_row_count,
            "last_investigation": {
                "current_task_id": memory.last_investigation.get("current_task_id"),
                "evidence_count": len(memory.last_investigation.get("evidence") or []),
                "evidence": [
                    {
                        "evidence_id": item.get("evidence_id"),
                        "task_id": item.get("task_id"),
                        "specialist": item.get("specialist"),
                        "summary": item.get("summary"),
                    }
                    for item in (memory.last_investigation.get("evidence") or [])[:8]
                ],
            },
        }

    @classmethod
    def _bounded_history(cls, history: list[dict[str, str]]) -> list[dict[str, str]]:
        bounded: list[dict[str, str]] = []
        total = 0
        for item in history[-cls._MAX_HISTORY_MESSAGES :]:
            role = str(item.get("role") or "unknown")[:32]
            content = str(item.get("content") or "")[: cls._MAX_HISTORY_CHARS_PER_MESSAGE]
            remaining = cls._MAX_HISTORY_TOTAL_CHARS - total
            if remaining <= 0:
                break
            content = content[:remaining]
            total += len(content)
            bounded.append({"role": role, "content": content})
        return bounded

    @staticmethod
    def _clarification(
        question: str,
        *,
        relation: ContextRelation,
        message: str,
        rationale: str,
        confidence: float = 1.0,
    ) -> ContextResolutionOutput:
        return ContextResolutionOutput(
            original_question=question,
            resolved_question=question,
            relation=relation,
            confidence=confidence,
            rationale=rationale,
            requires_clarification=True,
            clarification_question=message,
        )
