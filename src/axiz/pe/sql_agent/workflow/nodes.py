from __future__ import annotations

from uuid import UUID

from langgraph.types import interrupt

from axiz.pe.sql_agent.agents.conversation_context_agent import ConversationContextAgent
from axiz.pe.sql_agent.agents.explanation_agent import ExplanationAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.agents.result_verifier_agent import ResultVerifierAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.models.contracts import (
    ApprovalDecision,
    CostValidation,
    QueryResult,
    SecurityValidation,
)
from axiz.pe.sql_agent.models.state import AgentState
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.tools.llm_token_estimator import LLMApprovalTokenEstimator
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.sql_executor import PostgresQueryTool
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator


class WorkflowNodes:
    def __init__(
        self,
        *,
        settings: Settings,
        intent_agent: IntentDomainAgent,
        conversation_agent: ConversationContextAgent,
        semantic_agent: SemanticExplorerAgent,
        sql_agent: SqlGeneratorAgent,
        verifier_agent: ResultVerifierAgent,
        explanation_agent: ExplanationAgent,
        catalog: SemanticCatalogTool,
        validator: SqlSecurityValidator,
        query_tool: PostgresQueryTool,
        llm_approval_estimator: LLMApprovalTokenEstimator,
        runs: RunRepository,
    ) -> None:
        self.settings = settings
        self.intent_agent = intent_agent
        self.conversation_agent = conversation_agent
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.verifier_agent = verifier_agent
        self.explanation_agent = explanation_agent
        self.catalog = catalog
        self.validator = validator
        self.query_tool = query_tool
        self.llm_approval_estimator = llm_approval_estimator
        self.runs = runs

    async def classify(self, state: AgentState) -> AgentState:
        output = await self.intent_agent.classify(
            state["question"],
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
        context = await self.semantic_agent.explore(state["question"], domain)
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
            state["question"],
            state["semantic_context"],
        )
        return {
            "status": "completed",
            "answer": output.answer,
            "caveats": output.caveats,
            "key_findings": [],
            "visualization": {"type": "table", "title": "Semantic catalog"},
        }

    async def generate_sql(self, state: AgentState) -> AgentState:
        output = await self.sql_agent.generate(
            question=state["question"],
            semantic_context=state["semantic_context"],
            history=state.get("conversation_history", []),
            feedback=state.get("feedback_comment"),
            previous_sql=state.get("generated_sql"),
        )
        await self._audit(
            state,
            "sql_generated",
            {
                "sql": output.sql,
                "sources": output.source_objects,
                "repair_attempts": state.get("repair_attempts", 0),
            },
        )
        return {
            "generated_sql": output.sql.strip().rstrip(";"),
            "review_revision": state.get("review_revision", 0) + 1,
            "interpretation": output.interpretation,
            "assumptions": output.assumptions,
            "selected_metrics": output.selected_metrics,
            "selected_dimensions": output.selected_dimensions,
            "source_objects": output.source_objects,
            "feedback_comment": None,
            "security_validation": {},
            "cost_validation": {},
            "llm_approval_estimate": {},
        }

    async def estimate_llm_approval(self, state: AgentState) -> AgentState:
        estimate = self.llm_approval_estimator.estimate(
            question=state["question"],
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
        return {"approval_status": decision, "feedback_comment": comment}

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
            question=state["question"],
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
            question=state["question"],
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


def route_after_exploration(state: AgentState) -> str:
    return "answer_catalog" if state.get("intent") == "catalog_question" else "generate_sql"


def route_after_review(state: AgentState) -> str:
    decision = state.get("approval_status")
    if decision == ApprovalDecision.APPROVE.value:
        return "execute_sql"
    if decision == ApprovalDecision.REQUEST_CHANGES.value:
        return "generate_sql"
    return "rejected"


def route_after_security(state: AgentState) -> str:
    validation = state.get("security_validation", {})
    if validation.get("approved"):
        return "estimate_cost"
    if state.get("status") == "failed":
        return "end"
    return "generate_sql"


def route_after_cost(state: AgentState) -> str:
    return "estimate_llm_approval" if state.get("cost_validation", {}).get("approved") else "end"
