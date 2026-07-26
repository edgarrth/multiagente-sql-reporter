from __future__ import annotations

from uuid import UUID

from langgraph.types import interrupt

from axiz.pe.sql_agent.agents.context_resolver_agent import ContextResolverAgent
from axiz.pe.sql_agent.agents.conversation_context_agent import ConversationContextAgent
from axiz.pe.sql_agent.agents.explanation_agent import ExplanationAgent
from axiz.pe.sql_agent.agents.feedback_compliance_agent import FeedbackComplianceAgent
from axiz.pe.sql_agent.agents.feedback_interpreter_agent import FeedbackInterpreterAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.agents.result_verifier_agent import ResultVerifierAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.models.contracts import (
    ApprovalDecision,
    ContextRelation,
    ConversationMemory,
    CostValidation,
    FeedbackComplianceResult,
    FeedbackSemanticComplianceOutput,
    QueryResult,
    SecurityValidation,
    SqlFeedbackApplication,
    SqlFeedbackPlan,
    SqlGenerationOutput,
)
from axiz.pe.sql_agent.models.state import AgentState
from axiz.pe.sql_agent.workflow.context_routing import (
    route_after_context_resolution,
    route_after_exploration,
)
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.query_engines.base import QueryEngine
from axiz.pe.sql_agent.tools.llm_token_estimator import LLMApprovalTokenEstimator
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_compliance import SqlFeedbackComplianceValidator
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator


class WorkflowNodes:
    def __init__(
        self,
        *,
        settings: Settings,
        context_resolver_agent: ContextResolverAgent,
        intent_agent: IntentDomainAgent,
        conversation_agent: ConversationContextAgent,
        semantic_agent: SemanticExplorerAgent,
        sql_agent: SqlGeneratorAgent,
        feedback_interpreter_agent: FeedbackInterpreterAgent,
        feedback_compliance_agent: FeedbackComplianceAgent,
        verifier_agent: ResultVerifierAgent,
        explanation_agent: ExplanationAgent,
        catalog: SemanticCatalogTool,
        validator: SqlSecurityValidator,
        sql_feedback_applier: SqlFeedbackApplier,
        feedback_compliance_validator: SqlFeedbackComplianceValidator,
        query_engine: QueryEngine,
        llm_approval_estimator: LLMApprovalTokenEstimator,
        runs: RunRepository,
    ) -> None:
        self.settings = settings
        self.context_resolver_agent = context_resolver_agent
        self.intent_agent = intent_agent
        self.conversation_agent = conversation_agent
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.feedback_interpreter_agent = feedback_interpreter_agent
        self.feedback_compliance_agent = feedback_compliance_agent
        self.verifier_agent = verifier_agent
        self.explanation_agent = explanation_agent
        self.catalog = catalog
        self.validator = validator
        self.sql_feedback_applier = sql_feedback_applier
        self.feedback_compliance_validator = feedback_compliance_validator
        self.query_engine = query_engine
        self.query_tool = query_engine  # compatibility alias inside existing node code
        self.llm_approval_estimator = llm_approval_estimator
        self.runs = runs


    async def resolve_context(self, state: AgentState) -> AgentState:
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
        resolution = await self.context_resolver_agent.resolve(
            question=state["question"],
            memory=memory,
            history=state.get("conversation_history", []),
        )
        await self._audit(state, "context_resolved", resolution.model_dump(mode="json"))
        update: AgentState = {
            "resolved_question": resolution.resolved_question,
            "context_resolution": resolution.model_dump(mode="json"),
        }
        if resolution.relation == ContextRelation.ANALYTICAL_FOLLOW_UP:
            # A semantic follow-up is guaranteed to be an analytical SQL revision. Bypass the
            # generic intent classifier so it cannot be misrouted as a conversation question.
            update["intent"] = "analytical_query"
            update["domain"] = memory.last_domain
            update["domain_confidence"] = 1.0 if memory.last_domain else 0.0
        elif resolution.relation == ContextRelation.SESSION_REFERENCE:
            # Session references are conversational by contract and never propose new SQL.
            update["intent"] = "conversation_question"
            update["domain"] = None
            update["domain_confidence"] = 1.0
        if resolution.requires_clarification:
            update["clarification_question"] = resolution.clarification_question
        return update

    async def classify(self, state: AgentState) -> AgentState:
        output = await self.intent_agent.classify(
            state.get("resolved_question") or state["question"],
            self.catalog.list_domains(),
            state.get("conversation_history", []),
        )
        configured_domains = {item["name"] for item in self.catalog.list_domains()}
        selected_domain = output.domain if output.domain in configured_domains else None
        domainless_intents = {
            "capability_question",
            "conversation_question",
            "unsupported",
        }
        confidence = (
            output.confidence
            if selected_domain or output.intent.value in domainless_intents
            else 0.0
        )
        clarification = output.clarification_question
        if output.domain and selected_domain is None:
            clarification = (
                f"El dominio '{output.domain}' no está publicado. "
                "Indica uno de los dominios disponibles."
            )
        audit_payload = output.model_dump()
        audit_payload["validated_domain"] = selected_domain
        await self._audit(state, "intent_classified", audit_payload)
        return {
            "intent": output.intent.value,
            "domain": selected_domain,
            "domain_confidence": confidence,
            "clarification_question": clarification,
        }

    async def answer_conversation_context(self, state: AgentState) -> AgentState:
        output = await self.conversation_agent.answer(
            question=state["question"],
            history=state.get("conversation_history", []),
            memory=ConversationMemory.model_validate(
                state.get("conversation_memory") or {}
            ),
        )
        await self._audit(
            state,
            "conversation_context_answered",
            {"referenced_turns": output.referenced_turns},
        )
        return {
            "status": "completed",
            "answer": output.answer,
            "key_findings": [],
            "caveats": output.caveats,
            "visualization": {"type": "table", "title": "Contexto de la conversación"},
        }

    async def answer_capabilities(self, state: AgentState) -> AgentState:
        domains = self.catalog.list_domains()
        domain_lines = [
            f"- **{item['name']}**: {item.get('description') or 'Dominio semántico publicado'}"
            for item in domains
        ]
        published = "\n".join(domain_lines) or "- No hay dominios publicados actualmente."
        answer = (
            "Soy un agente analítico de solo lectura. Puedo:\n\n"
            "1. Clasificar tu intención y detectar el dominio de datos.\n"
            "2. Explorar el catálogo semántico, métricas, dimensiones, relaciones y ejemplos.\n"
            "3. Generar SQL y mostrarlo para aprobación humana antes de ejecutarlo.\n"
            "4. Corregir el SQL con tu feedback.\n"
            "5. Validar seguridad y costo, ejecutar únicamente consultas SELECT, verificar "
            "los resultados y explicarlos con tablas o gráficos.\n\n"
            "**Dominios publicados**\n" + published + "\n\n"
            "También puedo responder preguntas sobre definiciones del catálogo sin ejecutar SQL."
        )
        await self._audit(
            state,
            "capabilities_answered",
            {"published_domains": [item["name"] for item in domains]},
        )
        return {
            "status": "completed",
            "answer": answer,
            "key_findings": [],
            "caveats": [
                "No puedo insertar, actualizar ni eliminar datos.",
                "Solo consulto fuentes publicadas en el catálogo semántico.",
            ],
            "visualization": {"type": "table", "title": "Capacidades del agente"},
        }

    async def explore_semantics(self, state: AgentState) -> AgentState:
        domain = state.get("domain")
        if not domain:
            return {"status": "needs_clarification", "error": "No domain was selected"}
        context = await self.semantic_agent.explore(
            state.get("resolved_question") or state["question"], domain
        )
        await self._audit(
            state,
            "semantic_context_selected",
            {
                "domain": domain,
                "catalog_paths": [hit["path"] for hit in context["catalog_hits"]],
                "example_count": len(context["selected_examples"]),
            },
        )
        return {
            "semantic_context": context,
            "selected_examples": context["selected_examples"],
        }

    async def answer_catalog(self, state: AgentState) -> AgentState:
        output = await self.explanation_agent.answer_catalog_question(
            state.get("resolved_question") or state["question"],
            state["semantic_context"],
        )
        return {
            "status": "completed",
            "answer": output.answer,
            "caveats": output.caveats,
            "key_findings": [],
            "visualization": {"type": "table", "title": "Semantic catalog"},
        }

    async def interpret_feedback(self, state: AgentState) -> AgentState:
        feedback = (state.get("feedback_comment") or "").strip()
        semantic_context = dict(state.get("semantic_context") or {})
        if not semantic_context.get("semantic_symbols") and state.get("domain"):
            semantic_context["semantic_symbols"] = self.catalog.semantic_symbols(
                str(state["domain"])
            )
        plan = await self.feedback_interpreter_agent.interpret(
            feedback=feedback,
            previous_sql=state.get("generated_sql", ""),
            semantic_context=semantic_context,
            current_contract={
                "interpretation": state.get("interpretation"),
                "metrics": state.get("selected_metrics", []),
                "dimensions": state.get("selected_dimensions", []),
                "filters": state.get("selected_filters", []),
                "time_window": state.get("time_window"),
                "sources": state.get("source_objects", []),
            },
        )
        await self._audit(state, "feedback_interpreted", plan.model_dump(mode="json"))
        update: AgentState = {
            "feedback_plan": plan.model_dump(mode="json"),
            "feedback_compliance": {},
            "feedback_repair_attempts": 0,
        }
        if plan.requires_clarification:
            update["clarification_question"] = plan.clarification_question
            update["status"] = "needs_clarification"
        return update

    async def interpret_follow_up(self, state: AgentState) -> AgentState:
        """Treat an analytical follow-up as a governed delta over the last executed SQL."""
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
        previous_sql = (memory.last_sql or "").strip()
        if not previous_sql:
            return {"follow_up_change_plan": False}

        semantic_context = dict(state.get("semantic_context") or {})
        if not semantic_context.get("semantic_symbols") and state.get("domain"):
            semantic_context["semantic_symbols"] = self.catalog.semantic_symbols(
                str(state["domain"])
            )
        feedback = (state.get("question") or "").strip()
        plan = await self.feedback_interpreter_agent.interpret(
            feedback=feedback,
            previous_sql=previous_sql,
            semantic_context=semantic_context,
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
        await self._audit(
            state,
            "follow_up_change_plan_created",
            plan.model_dump(mode="json"),
        )
        update: AgentState = {
            "follow_up_change_plan": True,
            "feedback_comment": feedback,
            "feedback_plan": plan.model_dump(mode="json"),
            "feedback_compliance": {},
            "feedback_repair_attempts": 0,
            "previous_review_sql": previous_sql,
            "generated_sql": previous_sql,
            "interpretation": memory.last_interpretation or "",
            "selected_metrics": list(memory.last_metrics),
            "selected_dimensions": list(memory.last_dimensions),
            "selected_filters": [
                item.model_dump(mode="json") for item in memory.last_filters
            ],
            "time_window": (
                memory.last_time_window.model_dump(mode="json")
                if memory.last_time_window
                else None
            ),
            "source_objects": list(memory.last_source_objects),
        }
        if plan.requires_clarification:
            update["clarification_question"] = plan.clarification_question
            update["status"] = "needs_clarification"
        return update

    async def generate_sql(self, state: AgentState) -> AgentState:
        output = await self.sql_agent.generate(
            question=state.get("resolved_question") or state["question"],
            semantic_context=state["semantic_context"],
            history=state.get("conversation_history", []),
            structured_memory=state.get("conversation_memory", {}),
            feedback=state.get("feedback_comment"),
            previous_sql=state.get("generated_sql"),
            feedback_plan=state.get("feedback_plan"),
            prior_compliance=state.get("feedback_compliance"),
        )
        await self._audit(
            state,
            "sql_generated",
            {
                "sql": output.sql,
                "sources": output.source_objects,
                "repair_attempts": state.get("repair_attempts", 0),
                "feedback_repair_attempts": state.get("feedback_repair_attempts", 0),
            },
        )
        return {
            "generated_sql": output.sql,
            "review_revision": state.get("review_revision", 0) + 1,
            "interpretation": output.interpretation,
            "assumptions": output.assumptions,
            "selected_metrics": output.selected_metrics,
            "selected_dimensions": output.selected_dimensions,
            "selected_filters": [item.model_dump(mode="json") for item in output.selected_filters],
            "time_window": output.time_window.model_dump(mode="json") if output.time_window else None,
            "source_objects": output.source_objects,
            "feedback_application": {},
            "security_validation": {},
            "cost_validation": {},
            "llm_approval_estimate": {},
        }

    async def apply_feedback(self, state: AgentState) -> AgentState:
        plan_payload = state.get("feedback_plan") or {}
        plan = SqlFeedbackPlan.model_validate(plan_payload) if plan_payload else None
        application = self.sql_feedback_applier.apply(
            state["generated_sql"],
            plan,
            previous_sql=state.get("previous_review_sql"),
        )
        interpretation = self.sql_feedback_applier.reconcile_interpretation(
            state.get("interpretation", ""), application
        )
        await self._audit(
            state,
            "feedback_applied",
            application.model_dump(mode="json"),
        )
        update: AgentState = {
            "generated_sql": application.sql,
            "interpretation": interpretation,
            "assumptions": state.get("assumptions", []) + application.warnings,
            "selected_filters": self.sql_feedback_applier.reconcile_filters(
                state.get("selected_filters", []), plan
            ),
            "feedback_application": application.model_dump(mode="json"),
        }
        if plan and plan.strategy.value == "ast_only":
            if plan.summary and plan.summary.lower() not in interpretation.lower():
                update["interpretation"] = (
                    interpretation.rstrip().rstrip(".")
                    + f". Ajuste aplicado: {plan.summary.rstrip().rstrip('.').lower()}."
                )
            update["review_revision"] = state.get("review_revision", 0) + 1
        return update

    async def validate_feedback_compliance(self, state: AgentState) -> AgentState:
        plan_payload = state.get("feedback_plan") or {}
        if not plan_payload:
            return {
                "feedback_compliance": FeedbackComplianceResult(
                    compliant=True,
                    requested_changes=[],
                ).model_dump(mode="json"),
                "feedback_comment": None,
            }

        plan = SqlFeedbackPlan.model_validate(plan_payload)
        generated = SqlGenerationOutput(
            sql=state["generated_sql"],
            interpretation=state.get("interpretation", ""),
            assumptions=state.get("assumptions", []),
            selected_metrics=state.get("selected_metrics", []),
            selected_dimensions=state.get("selected_dimensions", []),
            selected_filters=state.get("selected_filters", []),
            time_window=state.get("time_window"),
            source_objects=state.get("source_objects", []),
        )
        application = SqlFeedbackApplication.model_validate(
            state.get("feedback_application") or {"sql": state["generated_sql"]}
        )
        if plan.strategy.value == "ast_only":
            semantic = FeedbackSemanticComplianceOutput(
                compliant=True,
                applied_changes=application.applied_changes,
                confidence=1.0,
                rationale="Plan completamente verificable mediante postcondiciones AST.",
            )
        else:
            semantic = await self.feedback_compliance_agent.validate(
                plan=plan,
                previous_sql=state.get("previous_review_sql") or state.get("generated_sql", ""),
                generated=generated,
                final_sql=state["generated_sql"],
                semantic_context=state.get("semantic_context", {}),
                governed_application=application.model_dump(mode="json"),
            )
        compliance = self.feedback_compliance_validator.validate(
            plan=plan,
            previous_sql=state.get("previous_review_sql") or "",
            final_sql=state["generated_sql"],
            generated=generated,
            application=application,
            semantic=semantic,
        )
        await self._audit(
            state,
            "feedback_compliance_validated",
            compliance.model_dump(mode="json"),
        )
        update: AgentState = {
            "feedback_compliance": compliance.model_dump(mode="json"),
        }
        if compliance.compliant:
            update["feedback_comment"] = None
            return update

        attempts = state.get("feedback_repair_attempts", 0) + 1
        update["feedback_repair_attempts"] = attempts
        if compliance.requires_clarification:
            update["status"] = "needs_clarification"
            update["clarification_question"] = compliance.clarification_question
            return update
        if attempts > self.settings.max_feedback_repair_attempts:
            update["status"] = "needs_clarification"
            update["clarification_question"] = (
                "No pude aplicar de forma verificable todos los cambios solicitados. "
                "Reformula el ajuste indicando métrica, dimensión, filtro o periodo esperado. "
                f"Cambios pendientes: {', '.join(compliance.missing_changes)}"
            )
            return update
        update["feedback_comment"] = (
            f"{plan.feedback}\n\nValidación de cumplimiento: "
            f"{compliance.retry_instruction or 'aplica todos los cambios faltantes.'}"
        )
        return update

    async def estimate_llm_approval(self, state: AgentState) -> AgentState:
        estimate = self.llm_approval_estimator.estimate(
            question=state.get("resolved_question") or state["question"],
            interpretation=state.get("interpretation", ""),
            sql=state["generated_sql"],
            security=SecurityValidation.model_validate(state["security_validation"]),
            cost=CostValidation.model_validate(state["cost_validation"]),
        )
        await self._audit(state, "llm_approval_estimated", estimate.model_dump())
        return {"llm_approval_estimate": estimate.model_dump(mode="json")}

    async def human_review(self, state: AgentState) -> AgentState:
        payload = {
            "run_id": state["run_id"],
            "revision": state.get("review_revision", 1),
            "question": state["question"],
            "resolved_question": state.get("resolved_question") or state["question"],
            "domain": state.get("domain"),
            "interpretation": state.get("interpretation", ""),
            "sql": state.get("generated_sql", ""),
            "assumptions": state.get("assumptions", []),
            "source_objects": state.get("source_objects", []),
        }
        feedback = interrupt(payload)
        decision = str(feedback.get("decision", "reject"))
        comment = feedback.get("comment")
        await self._audit(state, "human_review_received", feedback)
        update: AgentState = {"approval_status": decision, "feedback_comment": comment}
        if decision == ApprovalDecision.REQUEST_CHANGES.value:
            update["previous_review_sql"] = state.get("generated_sql", "")
        return update

    async def validate_security(self, state: AgentState) -> AgentState:
        domain = str(state["domain"])
        validation = self.validator.validate(
            state["generated_sql"],
            allowed_sources=self.catalog.allowed_sources(domain),
            policy=self.catalog.policies(domain),
        )
        await self._audit(state, "sql_security_validated", validation.model_dump())
        update: AgentState = {"security_validation": validation.model_dump()}
        if validation.approved and validation.normalized_sql:
            update["generated_sql"] = validation.normalized_sql
        else:
            attempts = state.get("repair_attempts", 0) + 1
            update["repair_attempts"] = attempts
            update["feedback_comment"] = (
                "Security validator rejected the SQL. Correct all issues: "
                + "; ".join(validation.violations)
            )
            if attempts > self.settings.max_sql_repair_attempts:
                update["status"] = "failed"
                update["error"] = "SQL failed security validation after configured retries"
        return update

    async def estimate_cost(self, state: AgentState) -> AgentState:
        security = state["security_validation"]
        estimate = await self.query_tool.estimate_cost(
            state["generated_sql"],
            tables=list(security.get("tables", [])),
        )
        await self._audit(state, "sql_cost_validated", estimate.model_dump())
        if not estimate.approved:
            return {
                "cost_validation": estimate.model_dump(),
                "status": "failed",
                "error": "Query cost policy rejected execution: " + "; ".join(estimate.warnings),
            }
        return {"cost_validation": estimate.model_dump()}

    async def execute_sql(self, state: AgentState) -> AgentState:
        result = await self.query_tool.execute(state["generated_sql"])
        await self._audit(
            state,
            "sql_executed",
            {
                "row_count": result.row_count,
                "elapsed_ms": result.elapsed_ms,
                "truncated": result.truncated,
            },
        )
        return {"query_result": result.model_dump(mode="json")}

    async def verify_result(self, state: AgentState) -> AgentState:
        result = QueryResult.model_validate(state["query_result"])
        verification = await self.verifier_agent.verify(
            question=state.get("resolved_question") or state["question"],
            interpretation=state["interpretation"],
            sql=state["generated_sql"],
            result=result,
        )
        await self._audit(state, "result_verified", verification.model_dump())
        return {"verification": verification.model_dump()}

    async def explain(self, state: AgentState) -> AgentState:
        from axiz.pe.sql_agent.models.contracts import VerificationOutput

        result = QueryResult.model_validate(state["query_result"])
        verification = VerificationOutput.model_validate(state["verification"])
        output = await self.explanation_agent.explain(
            question=state.get("resolved_question") or state["question"],
            interpretation=state["interpretation"],
            result=result,
            verification=verification,
        )
        await self._audit(state, "answer_generated", output.model_dump())
        return {
            "status": "completed",
            "answer": output.answer,
            "key_findings": output.key_findings,
            "caveats": output.caveats,
            "visualization": output.visualization.model_dump(),
        }

    async def unsupported(self, state: AgentState) -> AgentState:
        return {
            "status": "completed",
            "answer": (
                "La solicitud está fuera del alcance analítico configurado. "
                "Puedo responder preguntas sobre los dominios publicados en el catálogo semántico."
            ),
            "key_findings": [],
            "caveats": ["No se generó ni ejecutó SQL"],
            "visualization": {"type": "table", "title": "No data"},
        }

    async def clarification(self, state: AgentState) -> AgentState:
        return {
            "status": "needs_clarification",
            "answer": state.get("clarification_question")
            or "La pregunta es ambigua. Indica el dominio o la métrica que deseas consultar.",
            "key_findings": [],
            "caveats": ["No se generó ni ejecutó SQL"],
            "visualization": {"type": "table", "title": "Clarification required"},
        }

    async def rejected(self, state: AgentState) -> AgentState:
        return {
            "status": "rejected",
            "answer": "La consulta fue rechazada durante la revisión humana y no se ejecutó.",
            "key_findings": [],
            "caveats": [state.get("feedback_comment") or "Sin comentario"],
            "visualization": {"type": "table", "title": "Rejected"},
        }

    async def _audit(self, state: AgentState, event_type: str, payload: dict) -> None:
        await self.runs.audit(
            UUID(state["run_id"]),
            UUID(state["user_id"]),
            event_type,
            payload,
        )



def route_after_classification(state: AgentState) -> str:
    if state.get("intent") == "capability_question":
        return "answer_capabilities"
    if state.get("intent") == "conversation_question":
        return "answer_conversation_context"
    if state.get("intent") == "unsupported":
        return "unsupported"
    if not state.get("domain") or state.get("domain_confidence", 0) < 0.70:
        return "clarification"
    return "explore_semantics"



def route_after_review(state: AgentState) -> str:
    decision = state.get("approval_status")
    if decision == ApprovalDecision.APPROVE.value:
        return "execute_sql"
    if decision == ApprovalDecision.REQUEST_CHANGES.value:
        return "interpret_feedback"
    return "rejected"



def route_after_feedback_interpretation(state: AgentState) -> str:
    plan = state.get("feedback_plan", {})
    if plan.get("requires_clarification"):
        return "clarification"
    return "apply_feedback" if plan.get("strategy") == "ast_only" else "generate_sql"


def route_after_feedback_compliance(state: AgentState) -> str:
    compliance = state.get("feedback_compliance", {})
    if compliance.get("compliant"):
        return "validate_security"
    if state.get("status") == "needs_clarification":
        return "clarification"
    if state.get("status") == "failed":
        return "end"
    return "generate_sql"

def route_after_security(state: AgentState) -> str:
    validation = state.get("security_validation", {})
    if validation.get("approved"):
        return "estimate_cost"
    if state.get("status") == "failed":
        return "end"
    return "generate_sql"


def route_after_cost(state: AgentState) -> str:
    return "estimate_llm_approval" if state.get("cost_validation", {}).get("approved") else "end"
