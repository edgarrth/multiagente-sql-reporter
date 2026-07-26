from __future__ import annotations

import json
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    ContextRelation,
    ContextResolutionOutput,
    ConversationMemory,
)
from axiz.pe.sql_agent.services.llm import StructuredLLM


class ContextResolverAgent:
    """Classifies message dependency and resolves analytical follow-ups semantically.

    The resolver deliberately avoids domain-word and phrase heuristics. A structured LLM
    classifies whether the message is independent, changes a previous analytical request,
    references the session, or is ambiguous. Only analytical follow-ups inherit memory.
    """

    _CONFIDENCE_THRESHOLD = 0.72
    _MAX_HISTORY_MESSAGES = 6
    _MAX_HISTORY_CHARS_PER_MESSAGE = 1200
    _MAX_HISTORY_TOTAL_CHARS = 4800

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

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

        payload = {
            "current_message": normalized,
            "has_previous_analytical_request": bool(memory.last_resolved_question),
            "has_previous_approved_sql": bool(memory.last_sql),
            "structured_memory": self._memory_projection(memory),
            "recent_conversation": self._bounded_history(history),
        }
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
For analytical_follow_up with prior analytical memory, rewrite it as one standalone analytical
question, preserving the newest instruction and inheriting only necessary fields. Never invent
metrics, dimensions, filters, dates, sources, formulas, ordering, limits, or business definitions.
For analytical_follow_up without usable prior memory, require clarification.
For ambiguous, require one concise clarification question.
Do not generate SQL. The structured memory is the source of truth; recent conversation is only
linguistic support and must not override it. Answer in the user's language.
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
        return self._enforce_policy(output, question, memory)

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
            if not memory.last_resolved_question or not memory.last_sql:
                return self._clarification(
                    question,
                    relation=ContextRelation.ANALYTICAL_FOLLOW_UP,
                    message=(
                        output.clarification_question
                        or "No existe una consulta analítica anterior que pueda modificarse. "
                        "Describe la nueva consulta completa."
                    ),
                    rationale=output.rationale or "No prior analytical SQL is available.",
                    confidence=output.confidence,
                )
            if not output.resolved_question.strip():
                return self._clarification(
                    question,
                    relation=ContextRelation.AMBIGUOUS,
                    message="No pude construir una solicitud autocontenida. Reformula el cambio.",
                    rationale="The resolver returned an empty standalone question.",
                    confidence=output.confidence,
                )
            return output.model_copy(
                update={
                    "relation": ContextRelation.ANALYTICAL_FOLLOW_UP,
                    "is_follow_up": True,
                    "requires_sql_revision": True,
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
            "last_result_schema": list(memory.last_result_schema),
            "last_row_count": memory.last_row_count,
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
