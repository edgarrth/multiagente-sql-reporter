from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID, uuid4

from langgraph.types import Send, interrupt

from axiz.pe.sql_agent.agents.autonomous import (
    AutonomousComplexityRouterAgent,
    AutonomousSupervisorAgent,
    CriticAgent,
    InvestigationPlannerAgent,
)
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
    AutonomousBudget,
    AutonomousBudgetUsage,
    AutonomousInvestigationSummary,
    AutonomousRoutingDecision,
    AutonomousSynthesisOutput,
    ContextRelation,
    CriticReviewOutput,
    ConversationMemory,
    CostValidation,
    FeedbackComplianceResult,
    FeedbackSemanticComplianceOutput,
    EvidenceBackedFinding,
    InvestigationEvidence,
    InvestigationMode,
    InvestigationPlan,
    InvestigationQueryMode,
    InvestigationTask,
    InvestigationTaskStatus,
    InvestigationTrajectoryEvent,
    QueryResult,
    SecurityValidation,
    SqlFeedbackApplication,
    SqlFeedbackPlan,
    SqlGenerationOutput,
    SpecialistTaskOutput,
    SupervisorAction,
    SupervisorDecision,
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
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool
from axiz.pe.sql_agent.tools.investigation_governance import (
    InvestigationGovernanceError,
    InvestigationGovernancePolicy,
)
from axiz.pe.sql_agent.services.llm_usage import current_llm_usage_collector
from axiz.pe.sql_agent.services.specialist_graph_registry import SpecialistGraphRegistry
from axiz.pe.sql_agent.services.specialist_registry import SpecialistRegistry


class WorkflowNodes:
    def __init__(
        self,
        *,
        settings: Settings,
        context_resolver_agent: ContextResolverAgent,
        autonomous_router_agent: AutonomousComplexityRouterAgent,
        autonomous_supervisor_agent: AutonomousSupervisorAgent,
        investigation_planner_agent: InvestigationPlannerAgent,
        specialist_graph_registry: SpecialistGraphRegistry,
        critic_subgraph: Any,
        specialist_registry: SpecialistRegistry,
        investigation_governance: InvestigationGovernancePolicy,
        intent_agent: IntentDomainAgent,
        conversation_agent: ConversationContextAgent,
        semantic_agent: SemanticExplorerAgent,
        sql_agent: SqlGeneratorAgent,
        feedback_interpreter_agent: FeedbackInterpreterAgent,
        feedback_compliance_agent: FeedbackComplianceAgent,
        verifier_agent: ResultVerifierAgent,
        explanation_agent: ExplanationAgent,
        charts: ChartBuilderTool,
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
        self.autonomous_router_agent = autonomous_router_agent
        self.autonomous_supervisor_agent = autonomous_supervisor_agent
        self.investigation_planner_agent = investigation_planner_agent
        self.specialist_graph_registry = specialist_graph_registry
        self.critic_subgraph = critic_subgraph
        self.specialist_registry = specialist_registry
        self.investigation_governance = investigation_governance
        self.intent_agent = intent_agent
        self.conversation_agent = conversation_agent
        self.semantic_agent = semantic_agent
        self.sql_agent = sql_agent
        self.feedback_interpreter_agent = feedback_interpreter_agent
        self.feedback_compliance_agent = feedback_compliance_agent
        self.verifier_agent = verifier_agent
        self.explanation_agent = explanation_agent
        self.charts = charts
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
            "autonomous_available": self.settings.autonomous_society_enabled,
            "autonomous_enabled": False,
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

    @staticmethod
    def _evidence_projection(evidence: list[dict], max_rows: int = 20) -> list[dict]:
        projected: list[dict] = []
        for item in evidence:
            compact = dict(item)
            result = dict(compact.get("result") or {})
            result["rows"] = list(result.get("rows") or [])[:max_rows]
            compact["result"] = result
            projected.append(compact)
        return projected

    async def initialize_society(self, state: AgentState) -> AgentState:
        budget = AutonomousBudget(
            max_iterations=self.settings.autonomous_max_iterations,
            max_tasks=self.settings.autonomous_max_tasks,
            max_parallel_tasks=self.settings.autonomous_max_parallel_tasks,
            max_queries=self.settings.autonomous_max_queries,
            max_llm_tokens=self.settings.autonomous_max_llm_tokens,
            max_active_execution_seconds=self.settings.autonomous_max_active_execution_seconds,
            max_total_plan_cost=self.settings.autonomous_max_total_plan_cost,
            max_total_plan_rows=self.settings.autonomous_max_total_plan_rows,
            max_total_relation_bytes=self.settings.autonomous_max_total_relation_bytes,
            max_total_database_seconds=self.settings.autonomous_max_total_database_seconds,
        )
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
        await self._audit(
            state,
            "autonomous_society_initialized",
            {
                "architecture": "adaptive-router-supervisor-planner-parallel-specialist-subgraphs-critic",
                "budget": budget.model_dump(mode="json"),
                "specialists": self.specialist_registry.available_for_planning(),
                "hitl_required": True,
                "security_bypass_allowed": False,
                "permissions_mutable_by_agents": False,
            },
        )
        return {
            "autonomous_enabled": True,
            "autonomous_mode": "",
            "autonomous_routing_decision": {},
            "autonomous_plan": {},
            "autonomous_current_task_id": None,
            "autonomous_current_proposal_id": None,
            "autonomous_specialist_output": {},
            "autonomous_query_mode": InvestigationQueryMode.NEW_EVIDENCE.value,
            "autonomous_evidence": [],
            "autonomous_proposals": [],
            "autonomous_pending_proposals": [],
            "autonomous_proposal_updates": [],
            "autonomous_trajectory": [],
            "autonomous_trajectory_updates": [],
            "autonomous_trajectory_sequence": 0,
            "autonomous_critic_review": {},
            "autonomous_supervisor_decision": {},
            "autonomous_budget": budget.model_dump(mode="json"),
            "autonomous_budget_usage": AutonomousBudgetUsage().model_dump(mode="json"),
            "autonomous_iteration": 0,
            "autonomous_queries_executed": 0,
            "autonomous_rejected_conclusions": [],
            "autonomous_wave": 0,
            "autonomous_dispatch_task_ids": [],
            "autonomous_published_domains": self.catalog.list_domains(),
            "autonomous_previous_sql": memory.last_sql or "",
        }

    async def select_investigation_mode(self, state: AgentState) -> AgentState:
        """Choose the smallest sufficient autonomous path and validate it deterministically."""
        budget = AutonomousBudget.model_validate(state["autonomous_budget"])
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
        relation = str(
            (state.get("context_resolution") or {}).get(
                "relation", ContextRelation.INDEPENDENT_REQUEST.value
            )
        )
        question = state.get("resolved_question") or state["question"]
        if self.settings.autonomous_adaptive_routing_enabled:
            try:
                decision = await self.autonomous_router_agent.route(
                    question=question,
                    relation=relation,
                    domain=state.get("domain"),
                    memory=memory,
                    specialists=self.specialist_registry.available_for_planning(),
                    published_domains=self.catalog.list_domains(),
                    budget=budget,
                    catalog_fingerprint=self.catalog.fingerprint(),
                )
            except Exception as exc:
                decision = AutonomousRoutingDecision(
                    mode=InvestigationMode.FULL_INVESTIGATION,
                    task_objective=question,
                    complexity_signals=["router_unavailable_fallback"],
                    confidence=0.0,
                )
                await self._audit(
                    state,
                    "autonomous_routing_fallback",
                    {"error": str(exc), "fallback": decision.mode.value},
                )
        else:
            decision = AutonomousRoutingDecision(
                mode=InvestigationMode.FULL_INVESTIGATION,
                task_objective=question,
                complexity_signals=["adaptive_routing_disabled"],
            )

        if decision.requires_clarification:
            return {
                "status": "needs_clarification",
                "clarification_question": decision.clarification_question
                or "No pude seleccionar una estrategia de investigación segura.",
                "autonomous_routing_decision": decision.model_dump(mode="json"),
            }

        enabled_profiles = {
            profile.role: profile
            for profile in self.specialist_registry.executable_profiles()
        }
        is_follow_up = (
            relation == ContextRelation.ANALYTICAL_FOLLOW_UP.value
            and bool(memory.last_sql)
        )
        effective_query_mode = (
            InvestigationQueryMode.REVISE_PREVIOUS
            if is_follow_up
            else InvestigationQueryMode.NEW_EVIDENCE
        )

        if decision.mode == InvestigationMode.DIRECT_SPECIALIST:
            specialist_id = str(decision.specialist or "")
            profile = enabled_profiles.get(specialist_id)
            if profile is None:
                return {
                    "status": "needs_clarification",
                    "clarification_question": (
                        "La solicitud no pudo asignarse a un especialista habilitado con "
                        "contratos semánticos publicados."
                    ),
                    "autonomous_routing_decision": decision.model_dump(mode="json"),
                }
            published = {item["name"] for item in self.catalog.list_domains()}
            domain = decision.domain or state.get("domain")
            if not domain:
                candidates = [item for item in profile.domains if item in published]
                domain = candidates[0] if len(candidates) == 1 else None
            if (
                not domain
                or domain not in published
                or ("*" not in profile.domains and domain not in profile.domains)
            ):
                return {
                    "status": "needs_clarification",
                    "clarification_question": (
                        "El especialista seleccionado no dispone de un dominio semántico "
                        "publicado compatible con la solicitud."
                    ),
                    "autonomous_routing_decision": decision.model_dump(mode="json"),
                }

            objective = (decision.task_objective or question).strip()
            stable_material = "|".join(
                [
                    question,
                    specialist_id,
                    domain,
                    relation,
                    effective_query_mode.value,
                    self.catalog.fingerprint(),
                ]
            )
            task_id = "direct-" + hashlib.sha256(
                stable_material.encode("utf-8")
            ).hexdigest()[:12]
            task = InvestigationTask(
                task_id=task_id,
                title=decision.task_title or "Análisis solicitado",
                objective=objective,
                specialist=specialist_id,
                domain=domain,
                priority=100,
                expected_evidence=list(decision.expected_evidence),
                query_mode=effective_query_mode,
                status=InvestigationTaskStatus.IN_PROGRESS,
                attempts=1,
                wave=1,
                task_budget=profile.task_budget,
                specialist_question=objective,
            )
            plan = InvestigationPlan(
                objective=question,
                strategy=(
                    "Delegación adaptativa a un subgrafo especialista con una sola evidencia "
                    "gobernada; seguridad, costo, presupuesto y HITL permanecen obligatorios."
                ),
                tasks=[task],
                success_criteria=list(decision.expected_evidence)
                or ["Una evidencia SQL verificada responde la solicitud"],
                stop_conditions=["Evidencia verificada o presupuesto agotado"],
                confidence=decision.confidence,
                warnings=[],
            )
            try:
                governed = self.investigation_governance.validate_plan(
                    plan,
                    enabled_roles=self.specialist_registry.enabled_roles(),
                    allow_previous_sql_revision=is_follow_up,
                )
            except InvestigationGovernanceError as exc:
                return {
                    "status": "needs_clarification",
                    "clarification_question": str(exc),
                    "autonomous_routing_decision": decision.model_dump(mode="json"),
                }
            decision = decision.model_copy(
                update={
                    "domain": domain,
                    "query_mode": effective_query_mode,
                    "task_objective": objective,
                }
            )
            supervisor_decision = SupervisorDecision(
                action=SupervisorAction.DELEGATE,
                next_task_id=task_id,
                next_task_ids=[task_id],
                rationale="Delegación directa seleccionada por el router adaptativo.",
            )
            trajectory, sequence = self._append_trajectory(
                state,
                stage="adaptive_routing",
                actor="autonomous_router",
                action=InvestigationMode.DIRECT_SPECIALIST.value,
                task_id=task_id,
                specialist_id=specialist_id,
                wave=1,
                metadata={
                    "domain": domain,
                    "complexity_signals": decision.complexity_signals,
                },
            )
            trajectory, sequence = self._append_trajectory(
                {
                    **state,
                    "autonomous_trajectory": trajectory,
                    "autonomous_trajectory_sequence": sequence,
                },
                stage="supervisor",
                actor="autonomous_supervisor",
                action="delegate",
                task_id=task_id,
                specialist_id=specialist_id,
                wave=1,
                metadata={"routing_mode": InvestigationMode.DIRECT_SPECIALIST.value},
            )
            await self._audit(
                state,
                "autonomous_mode_selected",
                decision.model_dump(mode="json"),
            )
            return {
                "autonomous_mode": InvestigationMode.DIRECT_SPECIALIST.value,
                "autonomous_routing_decision": decision.model_dump(mode="json"),
                "autonomous_plan": governed.plan.model_dump(mode="json"),
                "autonomous_supervisor_decision": supervisor_decision.model_dump(mode="json"),
                "autonomous_dispatch_task_ids": [task_id],
                "autonomous_wave": 1,
                "autonomous_budget_usage": AutonomousBudgetUsage(
                    tasks_created=1
                ).model_dump(mode="json"),
                "autonomous_query_mode": effective_query_mode.value,
                "autonomous_trajectory": trajectory,
                "autonomous_trajectory_sequence": sequence,
            }

        decision = decision.model_copy(
            update={
                "mode": InvestigationMode.FULL_INVESTIGATION,
                "query_mode": effective_query_mode,
            }
        )
        trajectory, sequence = self._append_trajectory(
            state,
            stage="adaptive_routing",
            actor="autonomous_router",
            action=InvestigationMode.FULL_INVESTIGATION.value,
            metadata={"complexity_signals": decision.complexity_signals},
        )
        await self._audit(
            state,
            "autonomous_mode_selected",
            decision.model_dump(mode="json"),
        )
        return {
            "autonomous_mode": InvestigationMode.FULL_INVESTIGATION.value,
            "autonomous_routing_decision": decision.model_dump(mode="json"),
            "autonomous_query_mode": effective_query_mode.value,
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    def route_investigation_mode(self, state: AgentState):
        if state.get("status") == "needs_clarification":
            return "clarification"
        if state.get("status") == "failed":
            return "end"
        if state.get("autonomous_mode") == InvestigationMode.FULL_INVESTIGATION.value:
            return "plan_investigation"
        sends = self.specialist_wave_sends(state)
        return sends if sends else "direct_failure"

    @staticmethod
    def _specialist_id(value: object) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _append_trajectory(
        state: AgentState,
        *,
        stage: str,
        actor: str,
        action: str,
        task_id: str | None = None,
        specialist_id: str | None = None,
        wave: int | None = None,
        cache_hit: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        trajectory = list(state.get("autonomous_trajectory") or [])
        sequence = max(
            [int(item.get("sequence") or 0) for item in trajectory] +
            [int(state.get("autonomous_trajectory_sequence") or 0)]
        ) + 1
        event = InvestigationTrajectoryEvent(
            sequence=sequence,
            stage=stage,
            actor=actor,
            action=action,
            task_id=task_id,
            specialist_id=specialist_id,
            wave=wave,
            cache_hit=cache_hit,
            metadata=metadata or {},
        )
        trajectory.append(event.model_dump(mode="json"))
        return trajectory, sequence

    def _apply_profile_task_budgets(self, plan: InvestigationPlan) -> InvestigationPlan:
        tasks: list[InvestigationTask] = []
        for task in plan.tasks:
            specialist_id = self._specialist_id(task.specialist)
            profile = self.specialist_registry.profile(specialist_id)
            tasks.append(
                task.model_copy(
                    update={
                        "specialist": specialist_id,
                        "task_budget": profile.task_budget,
                    }
                )
            )
        return plan.model_copy(update={"tasks": tasks})

    async def plan_investigation(self, state: AgentState) -> AgentState:
        budget = AutonomousBudget.model_validate(state["autonomous_budget"])
        memory = ConversationMemory.model_validate(state.get("conversation_memory") or {})
        relation = str(
            (state.get("context_resolution") or {}).get(
                "relation", ContextRelation.INDEPENDENT_REQUEST.value
            )
        )
        is_follow_up = relation == ContextRelation.ANALYTICAL_FOLLOW_UP.value
        plan = await self.investigation_planner_agent.plan(
            question=state.get("resolved_question") or state["question"],
            memory=memory,
            specialists=self.specialist_registry.available_for_planning(),
            published_domains=self.catalog.list_domains(),
            context_relation=relation,
            budget=budget,
        )
        if is_follow_up and plan.tasks and not any(
            task.query_mode == InvestigationQueryMode.REVISE_PREVIOUS
            for task in plan.tasks
        ):
            first_task = max(plan.tasks, key=lambda item: item.priority)
            plan = plan.model_copy(
                update={
                    "tasks": [
                        task.model_copy(
                            update={"query_mode": InvestigationQueryMode.REVISE_PREVIOUS}
                        )
                        if task.task_id == first_task.task_id
                        else task
                        for task in plan.tasks
                    ]
                }
            )
        try:
            plan = self._apply_profile_task_budgets(plan)
            governed = self.investigation_governance.validate_plan(
                plan,
                enabled_roles=self.specialist_registry.enabled_roles(),
                allow_previous_sql_revision=is_follow_up and bool(memory.last_sql),
            )
        except (InvestigationGovernanceError, KeyError, ValueError) as exc:
            await self._audit(state, "investigation_plan_rejected", {"error": str(exc)})
            return {
                "status": "needs_clarification",
                "clarification_question": (
                    "No pude construir un plan de investigación compatible con los datos y "
                    f"presupuestos publicados: {exc}"
                ),
            }
        plan = governed.plan
        await self._audit(state, "investigation_planned", plan.model_dump(mode="json"))
        return {
            "autonomous_plan": plan.model_dump(mode="json"),
            "autonomous_budget_usage": AutonomousBudgetUsage(
                tasks_created=len(plan.tasks)
            ).model_dump(mode="json"),
        }

    def _budget_usage(self, state: AgentState) -> AutonomousBudgetUsage:
        collector = current_llm_usage_collector()
        llm_tokens = collector.summary().actual_total_tokens if collector else 0
        return self.investigation_governance.usage(state, llm_tokens=llm_tokens)

    def _ready_tasks(self, plan: InvestigationPlan) -> list[InvestigationTask]:
        completed = {
            item.task_id
            for item in plan.tasks
            if item.status == InvestigationTaskStatus.COMPLETED
        }
        return sorted(
            [
                item
                for item in plan.tasks
                if item.status in {
                    InvestigationTaskStatus.PENDING,
                    InvestigationTaskStatus.BLOCKED,
                }
                and set(item.dependencies).issubset(completed)
                and item.attempts < item.task_budget.max_attempts
            ],
            key=lambda item: (-item.priority, item.task_id),
        )

    async def supervisor_review(self, state: AgentState) -> AgentState:
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        evidence = list(state.get("autonomous_evidence") or [])
        usage = self._budget_usage(state)
        critic_payload = state.get("autonomous_critic_review") or {}
        critic = CriticReviewOutput.model_validate(critic_payload) if critic_payload else None
        hard_exhaustion = {
            "max_queries",
            "max_iterations",
            "max_llm_tokens",
            "max_active_execution_seconds",
            "max_total_plan_cost",
            "max_total_plan_rows",
            "max_total_relation_bytes",
            "max_total_database_seconds",
        }.intersection(usage.exhausted_reasons)
        ready = self._ready_tasks(plan)
        if hard_exhaustion and evidence:
            decision = SupervisorDecision(
                action=SupervisorAction.STOP_BUDGET,
                rationale="Se alcanzó un presupuesto gobernado: "
                + ", ".join(sorted(hard_exhaustion)),
            )
        elif not ready and evidence:
            decision = SupervisorDecision(
                action=SupervisorAction.FINALIZE,
                rationale="No quedan tareas ejecutables y existe evidencia verificada.",
            )
        else:
            decision = await self.autonomous_supervisor_agent.decide(
                question=state.get("resolved_question") or state["question"],
                plan=plan,
                evidence=self._evidence_projection(evidence),
                critic=critic,
                budget_usage=usage.model_dump(mode="json"),
                available_specialists=self.specialist_registry.available_for_planning(),
            )
        iteration = int(state.get("autonomous_iteration") or 0) + 1
        usage = usage.model_copy(update={"iterations": iteration})
        try:
            decision = self.investigation_governance.validate_supervisor_decision(
                decision,
                plan=plan,
                usage=usage,
                enabled_roles=self.specialist_registry.enabled_roles(),
                allow_previous_sql_revision=(
                    (state.get("context_resolution") or {}).get("relation")
                    == ContextRelation.ANALYTICAL_FOLLOW_UP.value
                    and bool((state.get("conversation_memory") or {}).get("last_sql"))
                ),
            )
        except InvestigationGovernanceError as exc:
            await self._audit(state, "supervisor_decision_rejected", {"error": str(exc)})
            if evidence:
                decision = SupervisorDecision(
                    action=SupervisorAction.STOP_BUDGET,
                    rationale=f"La política rechazó la decisión del supervisor: {exc}",
                )
            else:
                return {
                    "status": "needs_clarification",
                    "clarification_question": (
                        "La investigación no pudo delegarse dentro de las políticas configuradas: "
                        + str(exc)
                    ),
                }

        if decision.new_tasks:
            new_tasks: list[InvestigationTask] = []
            for item in decision.new_tasks:
                profile = self.specialist_registry.profile(self._specialist_id(item.specialist))
                new_tasks.append(
                    item.model_copy(
                        update={
                            "specialist": profile.role,
                            "task_budget": profile.task_budget,
                            "status": InvestigationTaskStatus.PENDING,
                        }
                    )
                )
            plan = plan.model_copy(update={"tasks": list(plan.tasks) + new_tasks})

        selected_ids = list(decision.next_task_ids)
        if decision.next_task_id and decision.next_task_id not in selected_ids:
            selected_ids.append(decision.next_task_id)
        if decision.action in {
            SupervisorAction.DELEGATE,
            SupervisorAction.REQUEST_MORE_EVIDENCE,
            SupervisorAction.REJECT_CONCLUSION,
        } and not selected_ids:
            selected_ids = [item.task_id for item in self._ready_tasks(plan)]
        max_parallel = AutonomousBudget.model_validate(
            state["autonomous_budget"]
        ).max_parallel_tasks
        selected_ids = selected_ids[:max_parallel]
        wave = int(state.get("autonomous_wave") or 0) + (1 if selected_ids else 0)
        updated_tasks: list[InvestigationTask] = []
        for item in plan.tasks:
            if item.task_id in selected_ids:
                updated_tasks.append(
                    item.model_copy(
                        update={
                            "status": InvestigationTaskStatus.IN_PROGRESS,
                            "attempts": item.attempts + 1,
                            "replans": item.replans + (1 if item.status == InvestigationTaskStatus.BLOCKED else 0),
                            "wave": wave,
                        }
                    )
                )
            else:
                updated_tasks.append(item)
        plan = plan.model_copy(update={"tasks": updated_tasks})
        rejected = list(state.get("autonomous_rejected_conclusions") or [])
        rejected.extend(decision.rejected_conclusions)
        await self._audit(
            state,
            "supervisor_decision_recorded",
            {
                **decision.model_dump(mode="json"),
                "selected_task_ids": selected_ids,
                "wave": wave,
            },
        )
        trajectory, sequence = self._append_trajectory(
            state,
            stage="supervisor",
            actor="autonomous_supervisor",
            action=decision.action.value,
            wave=wave or None,
            metadata={"selected_task_ids": selected_ids},
        )
        return {
            "autonomous_plan": plan.model_dump(mode="json"),
            "autonomous_supervisor_decision": decision.model_dump(mode="json"),
            "autonomous_budget_usage": usage.model_dump(mode="json"),
            "autonomous_iteration": iteration,
            "autonomous_dispatch_task_ids": selected_ids,
            "autonomous_wave": wave,
            "autonomous_rejected_conclusions": rejected,
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    def specialist_wave_sends(self, state: AgentState):
        if state.get("status") in {"failed", "needs_clarification"}:
            return []
        decision = state.get("autonomous_supervisor_decision") or {}
        action = decision.get("action")
        if action in {SupervisorAction.FINALIZE.value, SupervisorAction.STOP_BUDGET.value}:
            return []
        selected = set(state.get("autonomous_dispatch_task_ids") or [])
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        sends = []
        for task in plan.tasks:
            if task.task_id not in selected:
                continue
            specialist_id = self._specialist_id(task.specialist)
            node_name = self.specialist_graph_registry.node_name(specialist_id)
            payload = dict(state)
            payload["autonomous_dispatch_task"] = task.model_dump(mode="json")
            sends.append(Send(node_name, payload))
        return sends

    def route_supervisor_dispatch(self, state: AgentState):
        """Route to terminal nodes or dynamically fan out specialist subgraphs."""
        if state.get("status") == "needs_clarification":
            return "clarification"
        if state.get("status") == "failed":
            return "end"
        decision = state.get("autonomous_supervisor_decision") or {}
        action = decision.get("action")
        if action in {SupervisorAction.FINALIZE.value, SupervisorAction.STOP_BUDGET.value}:
            return "synthesize_investigation"
        sends = self.specialist_wave_sends(state)
        if sends:
            return sends
        return "critic_review" if state.get("autonomous_evidence") else "clarification"

    async def collect_specialist_wave(self, state: AgentState) -> AgentState:
        existing = {
            item.get("proposal_id"): item
            for item in list(state.get("autonomous_proposals") or [])
            if item.get("proposal_id")
        }
        for item in list(state.get("autonomous_proposal_updates") or []):
            if item.get("proposal_id"):
                existing[item["proposal_id"]] = item
        proposals = list(existing.values())
        selected_ids = set(state.get("autonomous_dispatch_task_ids") or [])
        wave_proposals = [
            item
            for item in proposals
            if item.get("task_id") in selected_ids
            and int(item.get("wave") or 0) == int(state.get("autonomous_wave") or 0)
        ]
        pending = [
            item
            for item in wave_proposals
            if item.get("status") in {"ready", "cache_hit"}
        ]
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        statuses = {item.get("task_id"): item for item in wave_proposals}
        tasks: list[InvestigationTask] = []
        for task in plan.tasks:
            proposal = statuses.get(task.task_id)
            if proposal is None:
                tasks.append(task)
                continue
            if proposal.get("status") in {"ready", "cache_hit"}:
                status = InvestigationTaskStatus.AWAITING_APPROVAL
                block_reason = None
            elif proposal.get("status") == "blocked":
                status = InvestigationTaskStatus.BLOCKED
                block_reason = proposal.get("block_reason")
            else:
                status = InvestigationTaskStatus.FAILED
                block_reason = proposal.get("block_reason")
            tasks.append(
                task.model_copy(update={"status": status, "block_reason": block_reason})
            )
        plan = plan.model_copy(update={"tasks": tasks})
        usage = AutonomousBudgetUsage.model_validate(
            state.get("autonomous_budget_usage") or {}
        )
        usage.parallel_waves += 1
        usage.cache_hits += sum(1 for item in wave_proposals if item.get("cache_hit"))
        trajectory = list(state.get("autonomous_trajectory") or [])
        seen = {
            (
                item.get("stage"),
                item.get("task_id"),
                item.get("action"),
                item.get("wave"),
            )
            for item in trajectory
        }
        action_order = {
            "security_validated": 1,
            "cost_validated": 2,
            "proposal_created": 3,
        }
        next_sequence = max(
            [int(item.get("sequence") or 0) for item in trajectory]
            + [int(state.get("autonomous_trajectory_sequence") or 0)]
        )
        updates = sorted(
            list(state.get("autonomous_trajectory_updates") or []),
            key=lambda item: (
                int(item.get("wave") or 0),
                str(item.get("task_id") or ""),
                action_order.get(str(item.get("action") or ""), 99),
                str(item.get("actor") or ""),
            ),
        )
        for item in updates:
            key = (
                item.get("stage"),
                item.get("task_id"),
                item.get("action"),
                item.get("wave"),
            )
            if key not in seen:
                next_sequence += 1
                normalized = dict(item)
                normalized["sequence"] = next_sequence
                trajectory.append(normalized)
                seen.add(key)
        await self._audit(
            state,
            "specialist_wave_collected",
            {
                "wave": state.get("autonomous_wave"),
                "proposal_count": len(wave_proposals),
                "pending_approval_count": len(pending),
            },
        )
        return {
            "autonomous_plan": plan.model_dump(mode="json"),
            "autonomous_proposals": proposals,
            "autonomous_pending_proposals": pending,
            "autonomous_budget_usage": usage.model_dump(mode="json"),
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": next_sequence,
            "autonomous_dispatch_task_ids": [],
        }

    async def select_next_proposal(self, state: AgentState) -> AgentState:
        pending = list(state.get("autonomous_pending_proposals") or [])
        if not pending:
            return {"autonomous_current_task_id": None, "autonomous_current_proposal_id": None}
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        priorities = {item.task_id: item.priority for item in plan.tasks}
        pending.sort(
            key=lambda item: (
                -priorities.get(item.get("task_id"), 0),
                item.get("task_id", ""),
            )
        )
        usage = self._budget_usage(state)
        blocked_ids: dict[str, str] = {}
        selected: dict[str, Any] | None = None
        remaining: list[dict[str, Any]] = []
        trajectory = list(state.get("autonomous_trajectory") or [])
        sequence = int(state.get("autonomous_trajectory_sequence") or 0)

        for proposal in pending:
            if selected is not None:
                remaining.append(proposal)
                continue
            cost = CostValidation.model_validate(
                proposal.get("cost_validation") or {"approved": False}
            )
            violations = self.investigation_governance.proposal_budget_violations(
                usage, cost
            )
            if violations:
                reason = "La propuesta excede el presupuesto acumulado: " + ", ".join(violations)
                blocked_ids[str(proposal.get("task_id") or "")] = reason
                updated = dict(proposal)
                updated["status"] = "blocked"
                updated["block_reason"] = reason
                for index, stored in enumerate(state.get("autonomous_proposals") or []):
                    if stored.get("proposal_id") == proposal.get("proposal_id"):
                        proposals = list(state.get("autonomous_proposals") or [])
                        proposals[index] = updated
                        state = {**state, "autonomous_proposals": proposals}
                        break
                trajectory, sequence = self._append_trajectory(
                    {
                        **state,
                        "autonomous_trajectory": trajectory,
                        "autonomous_trajectory_sequence": sequence,
                    },
                    stage="governance",
                    actor="investigation_governance",
                    action="proposal_blocked_by_global_budget",
                    task_id=proposal.get("task_id"),
                    specialist_id=proposal.get("specialist_id"),
                    wave=int(proposal.get("wave") or 0),
                    metadata={
                        "proposal_id": proposal.get("proposal_id"),
                        "violations": violations,
                    },
                )
                continue
            selected = proposal

        if blocked_ids:
            plan = plan.model_copy(
                update={
                    "tasks": [
                        item.model_copy(
                            update={
                                "status": InvestigationTaskStatus.BLOCKED,
                                "block_reason": blocked_ids[item.task_id],
                            }
                        )
                        if item.task_id in blocked_ids
                        else item
                        for item in plan.tasks
                    ]
                }
            )

        proposals = list(state.get("autonomous_proposals") or [])
        for index, stored in enumerate(proposals):
            task_id = str(stored.get("task_id") or "")
            if task_id in blocked_ids:
                updated = dict(stored)
                updated["status"] = "blocked"
                updated["block_reason"] = blocked_ids[task_id]
                proposals[index] = updated

        if selected is None:
            return {
                "autonomous_plan": plan.model_dump(mode="json"),
                "autonomous_proposals": proposals,
                "autonomous_pending_proposals": [],
                "autonomous_current_task_id": None,
                "autonomous_current_proposal_id": None,
                "autonomous_trajectory": trajectory,
                "autonomous_trajectory_sequence": sequence,
            }

        proposal = selected
        for index, stored in enumerate(proposals):
            if stored.get("proposal_id") == proposal.get("proposal_id"):
                updated = dict(stored)
                updated["status"] = "awaiting_hitl"
                proposals[index] = updated
                break
        trajectory, sequence = self._append_trajectory(
            {
                **state,
                "autonomous_trajectory": trajectory,
                "autonomous_trajectory_sequence": sequence,
            },
            stage="proposal_queue",
            actor="autonomous_supervisor",
            action="proposal_selected_for_hitl",
            task_id=proposal.get("task_id"),
            specialist_id=proposal.get("specialist_id"),
            wave=int(proposal.get("wave") or 0),
            cache_hit=bool(proposal.get("cache_hit")),
            metadata={"proposal_id": proposal.get("proposal_id")},
        )
        return {
            "autonomous_plan": plan.model_dump(mode="json"),
            "autonomous_proposals": proposals,
            "autonomous_pending_proposals": remaining,
            "autonomous_current_task_id": proposal["task_id"],
            "autonomous_current_proposal_id": proposal["proposal_id"],
            "autonomous_query_mode": next(
                (
                    item.query_mode.value
                    for item in plan.tasks
                    if item.task_id == proposal["task_id"]
                ),
                InvestigationQueryMode.NEW_EVIDENCE.value,
            ),
            "resolved_question": proposal["question"],
            "domain": proposal.get("domain"),
            "domain_confidence": 1.0,
            "intent": "analytical_query",
            "semantic_context": proposal.get("semantic_context") or {},
            "generated_sql": proposal.get("sql") or "",
            "interpretation": proposal.get("interpretation") or "",
            "assumptions": proposal.get("assumptions") or [],
            "selected_metrics": proposal.get("selected_metrics") or [],
            "selected_dimensions": proposal.get("selected_dimensions") or [],
            "selected_filters": proposal.get("selected_filters") or [],
            "time_window": proposal.get("time_window"),
            "source_objects": proposal.get("source_objects") or [],
            "security_validation": proposal.get("security_validation") or {},
            "cost_validation": proposal.get("cost_validation") or {},
            "previous_review_sql": proposal.get("sql") or "",
            "feedback_plan": {},
            "feedback_application": {},
            "feedback_compliance": {},
            "review_revision": int(state.get("review_revision") or 0) + 1,
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def reject_autonomous_proposal(self, state: AgentState) -> AgentState:
        task_id = str(state.get("autonomous_current_task_id") or "")
        proposal_id = str(state.get("autonomous_current_proposal_id") or "")
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        tasks = [
            item.model_copy(
                update={
                    "status": InvestigationTaskStatus.REJECTED,
                    "block_reason": state.get("feedback_comment") or "Rechazada por HITL",
                }
            )
            if item.task_id == task_id
            else item
            for item in plan.tasks
        ]
        pending = [
            item
            for item in list(state.get("autonomous_pending_proposals") or [])
            if item.get("proposal_id") != proposal_id
        ]
        proposals = []
        for item in list(state.get("autonomous_proposals") or []):
            updated = dict(item)
            if updated.get("proposal_id") == proposal_id:
                updated["status"] = "rejected"
                updated["block_reason"] = state.get("feedback_comment") or "Rechazada por HITL"
            proposals.append(updated)
        await self._audit(
            state,
            "autonomous_proposal_rejected",
            {"task_id": task_id, "proposal_id": proposal_id},
        )
        trajectory, sequence = self._append_trajectory(
            state,
            stage="hitl",
            actor="human",
            action="proposal_rejected",
            task_id=task_id,
            metadata={"proposal_id": proposal_id},
        )
        return {
            "autonomous_plan": plan.model_copy(update={"tasks": tasks}).model_dump(mode="json"),
            "autonomous_pending_proposals": pending,
            "autonomous_proposals": proposals,
            "autonomous_current_task_id": None,
            "autonomous_current_proposal_id": None,
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def record_evidence(self, state: AgentState) -> AgentState:
        result = QueryResult.model_validate(state["query_result"])
        from axiz.pe.sql_agent.models.contracts import VerificationOutput

        verification = VerificationOutput.model_validate(state["verification"])
        explanation = await self.explanation_agent.explain(
            question=state.get("resolved_question") or state["question"],
            interpretation=state.get("interpretation", ""),
            result=result,
            verification=verification,
        )
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        task_id = str(state.get("autonomous_current_task_id") or "")
        proposal_id = str(state.get("autonomous_current_proposal_id") or "")
        task = next(item for item in plan.tasks if item.task_id == task_id)
        evidence = InvestigationEvidence(
            evidence_id=f"evidence-{uuid4().hex[:12]}",
            task_id=task_id,
            specialist=task.specialist,
            question=state.get("resolved_question") or state["question"],
            interpretation=state.get("interpretation", ""),
            sql=state["generated_sql"],
            domain=str(state["domain"]),
            source_objects=list(state.get("source_objects") or []),
            result=result.model_dump(mode="json"),
            verification=verification.model_dump(mode="json"),
            summary=explanation.answer,
            findings=explanation.key_findings,
            caveats=explanation.caveats,
        )
        evidence_payload = evidence.model_dump(mode="json")
        evidence_payload["proposal_id"] = proposal_id
        evidence_payload["security_validation"] = dict(state.get("security_validation") or {})
        evidence_payload["cost_validation"] = dict(state.get("cost_validation") or {})
        evidence_list = list(state.get("autonomous_evidence") or []) + [evidence_payload]
        completed_task = task.model_copy(update={"status": InvestigationTaskStatus.COMPLETED})
        plan = plan.model_copy(
            update={
                "tasks": [completed_task if item.task_id == task_id else item for item in plan.tasks]
            }
        )
        pending = [
            item
            for item in list(state.get("autonomous_pending_proposals") or [])
            if item.get("proposal_id") != proposal_id
        ]
        usage = AutonomousBudgetUsage.model_validate(
            state.get("autonomous_budget_usage") or {}
        )
        cost = CostValidation.model_validate(state.get("cost_validation") or {"approved": False})
        usage.queries_executed += 1
        usage.total_plan_cost += float(cost.total_cost or 0.0)
        usage.total_plan_rows += int(cost.max_node_rows or cost.plan_rows or 0)
        usage.total_relation_bytes += int(cost.relation_bytes or 0)
        usage.total_database_seconds += float(result.elapsed_ms or 0.0) / 1000.0
        proposals = []
        for item in list(state.get("autonomous_proposals") or []):
            updated = dict(item)
            if updated.get("proposal_id") == proposal_id:
                updated.update(
                    {
                        "status": "executed",
                        "sql": state.get("generated_sql") or updated.get("sql") or "",
                        "interpretation": state.get("interpretation") or "",
                        "selected_metrics": list(state.get("selected_metrics") or []),
                        "selected_dimensions": list(state.get("selected_dimensions") or []),
                        "selected_filters": list(state.get("selected_filters") or []),
                        "time_window": state.get("time_window"),
                        "source_objects": list(state.get("source_objects") or []),
                        "security_validation": dict(state.get("security_validation") or {}),
                        "cost_validation": dict(state.get("cost_validation") or {}),
                    }
                )
            proposals.append(updated)
        await self._audit(state, "investigation_evidence_recorded", evidence_payload)
        trajectory, sequence = self._append_trajectory(
            state,
            stage="evidence_ledger",
            actor=self._specialist_id(task.specialist),
            action="evidence_recorded",
            task_id=task_id,
            specialist_id=self._specialist_id(task.specialist),
            wave=task.wave,
            metadata={"evidence_id": evidence.evidence_id, "proposal_id": proposal_id},
        )
        return {
            "autonomous_plan": plan.model_dump(mode="json"),
            "autonomous_evidence": evidence_list,
            "autonomous_pending_proposals": pending,
            "autonomous_proposals": proposals,
            "autonomous_budget_usage": usage.model_dump(mode="json"),
            "autonomous_queries_executed": usage.queries_executed,
            "autonomous_current_task_id": None,
            "autonomous_current_proposal_id": None,
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def direct_failure(self, state: AgentState) -> AgentState:
        """Finish a direct delegation safely when no executable proposal was produced."""
        plan_payload = state.get("autonomous_plan") or {}
        proposals = list(state.get("autonomous_proposals") or [])
        reasons: list[str] = []
        for proposal in proposals:
            if proposal.get("block_reason"):
                reasons.append(str(proposal["block_reason"]))
        try:
            plan = InvestigationPlan.model_validate(plan_payload)
            for task in plan.tasks:
                if task.status in {
                    InvestigationTaskStatus.BLOCKED,
                    InvestigationTaskStatus.REJECTED,
                } and task.block_reason:
                    reasons.append(task.block_reason)
        except Exception:
            pass
        reason = reasons[0] if reasons else (
            "El especialista no produjo una propuesta SQL gobernada dentro de los presupuestos."
        )
        trajectory, sequence = self._append_trajectory(
            state,
            stage="adaptive_routing",
            actor="governance",
            action="direct_specialist_stopped",
            metadata={"reason": reason},
        )
        await self._audit(
            state,
            "direct_specialist_stopped",
            {"reason": reason, "proposal_count": len(proposals)},
        )
        return {
            "status": "failed",
            "error": reason,
            "answer": "No fue posible completar la consulta dentro de los controles gobernados.",
            "key_findings": [],
            "caveats": [reason],
            "visualization": {"type": "table", "title": "Sin resultado"},
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def synthesize_direct_investigation(self, state: AgentState) -> AgentState:
        """Finalize one verified evidence package without extra planner/critic/synthesis calls."""
        evidence_payload = list(state.get("autonomous_evidence") or [])
        if not evidence_payload:
            return await self.direct_failure(state)
        primary = evidence_payload[-1]
        evidence = InvestigationEvidence.model_validate(primary)
        result = QueryResult.model_validate(evidence.result)
        findings_text = list(evidence.findings) or [evidence.summary]
        findings = [
            EvidenceBackedFinding(
                statement=text,
                evidence_ids=[evidence.evidence_id],
                confidence=1.0,
                limitations=list(evidence.caveats),
            )
            for text in findings_text
            if str(text).strip()
        ]
        if not findings:
            findings = [
                EvidenceBackedFinding(
                    statement="La evidencia ejecutada responde la solicitud analítica.",
                    evidence_ids=[evidence.evidence_id],
                    limitations=list(evidence.caveats),
                )
            ]
        visualization = self.charts.build(result, title="Resultado")
        trajectory, sequence = self._append_trajectory(
            state,
            stage="synthesis",
            actor="governed_direct_synthesis",
            action="direct_evidence_finalized",
            task_id=evidence.task_id,
            specialist_id=self._specialist_id(evidence.specialist),
            metadata={"evidence_id": evidence.evidence_id},
        )
        await self._audit(
            state,
            "direct_autonomous_investigation_completed",
            {
                "evidence_id": evidence.evidence_id,
                "finding_count": len(findings),
                "llm_synthesis_skipped": True,
            },
        )
        return {
            "status": "completed",
            "answer": evidence.summary,
            "key_findings": [item.statement for item in findings],
            "caveats": list(evidence.caveats),
            "query_result": evidence.result,
            "generated_sql": evidence.sql,
            "interpretation": evidence.interpretation,
            "domain": evidence.domain,
            "source_objects": list(evidence.source_objects),
            "visualization": visualization.model_dump(mode="json"),
            "autonomous_primary_evidence_id": evidence.evidence_id,
            "autonomous_grounded_findings": [
                item.model_dump(mode="json") for item in findings
            ],
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def critic_review(self, state: AgentState) -> AgentState:
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        usage = self._budget_usage(state)
        result = await self.critic_subgraph.ainvoke(
            {
                "question": state.get("question", ""),
                "plan": plan.model_dump(mode="json"),
                "evidence": self._evidence_projection(
                    list(state.get("autonomous_evidence") or []), max_rows=20
                ),
                "budget_remaining": {
                    "iterations": max(0, self.settings.autonomous_max_iterations - usage.iterations),
                    "tasks": max(0, self.settings.autonomous_max_tasks - usage.tasks_created),
                    "queries": max(0, self.settings.autonomous_max_queries - usage.queries_executed),
                    "llm_tokens": max(0, self.settings.autonomous_max_llm_tokens - usage.llm_tokens),
                },
                "available_specialists": self.specialist_registry.available_for_planning(),
            }
        )
        review = CriticReviewOutput.model_validate(result["review"])
        await self._audit(state, "critic_review_completed", review.model_dump(mode="json"))
        trajectory, sequence = self._append_trajectory(
            state,
            stage="critic",
            actor="critic",
            action="evidence_reviewed",
            metadata={
                "ready_to_finalize": review.ready_to_finalize,
                "accepted_evidence_ids": review.accepted_evidence_ids,
            },
        )
        return {
            "autonomous_critic_review": review.model_dump(mode="json"),
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
        }

    async def synthesize_investigation(self, state: AgentState) -> AgentState:
        plan = InvestigationPlan.model_validate(state["autonomous_plan"])
        evidence_payload = list(state.get("autonomous_evidence") or [])
        if not evidence_payload:
            return {"status": "failed", "error": "La investigación terminó sin evidencia ejecutada"}
        critic_payload = state.get("autonomous_critic_review") or {}
        critic = CriticReviewOutput.model_validate(critic_payload) if critic_payload else None
        output = await self.autonomous_supervisor_agent.synthesize(
            question=state["question"],
            plan=plan,
            evidence=self._evidence_projection(evidence_payload, max_rows=50),
            critic=critic,
            rejected_conclusions=list(state.get("autonomous_rejected_conclusions") or []),
        )
        evidence_ids = {str(item.get("evidence_id")) for item in evidence_payload}
        invalid_findings = [
            item.statement
            for item in output.findings
            if not set(item.evidence_ids).issubset(evidence_ids)
        ]
        if invalid_findings:
            return {
                "status": "failed",
                "error": "La síntesis incluyó hallazgos sin evidencia válida: "
                + "; ".join(invalid_findings),
            }
        primary = next(
            (item for item in evidence_payload if item.get("evidence_id") == output.primary_evidence_id),
            evidence_payload[-1],
        )
        result = QueryResult.model_validate(primary["result"])
        visualization = self.charts.build(result, title="Evidencia principal")
        await self._audit(
            state,
            "autonomous_investigation_completed",
            {
                "evidence_count": len(evidence_payload),
                "primary_evidence_id": primary.get("evidence_id"),
                "grounded_findings": [item.model_dump(mode="json") for item in output.findings],
            },
        )
        trajectory, sequence = self._append_trajectory(
            state,
            stage="synthesis",
            actor="autonomous_supervisor",
            action="investigation_finalized",
            metadata={
                "primary_evidence_id": primary.get("evidence_id"),
                "finding_count": len(output.findings),
            },
        )
        return {
            "status": "completed",
            "answer": output.answer,
            "key_findings": output.key_findings,
            "caveats": output.caveats,
            "query_result": primary["result"],
            "generated_sql": primary["sql"],
            "interpretation": primary["interpretation"],
            "domain": primary["domain"],
            "source_objects": primary.get("source_objects") or [],
            "visualization": visualization.model_dump(mode="json"),
            "autonomous_primary_evidence_id": primary.get("evidence_id"),
            "autonomous_grounded_findings": [
                item.model_dump(mode="json") for item in output.findings
            ],
            "autonomous_trajectory": trajectory,
            "autonomous_trajectory_sequence": sequence,
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
            autonomous=bool(state.get("autonomous_enabled")),
            existing_evidence_count=len(state.get("autonomous_evidence") or []),
        )
        await self._audit(state, "llm_approval_estimated", estimate.model_dump())
        return {"llm_approval_estimate": estimate.model_dump(mode="json")}

    def _autonomous_summary(self, state: AgentState) -> AutonomousInvestigationSummary | None:
        if not state.get("autonomous_enabled"):
            return None
        plan_payload = state.get("autonomous_plan") or {}
        budget_payload = state.get("autonomous_budget") or {}
        usage = self._budget_usage(state)
        return AutonomousInvestigationSummary(
            enabled=True,
            plan=InvestigationPlan.model_validate(plan_payload) if plan_payload else None,
            current_task_id=state.get("autonomous_current_task_id"),
            proposals=list(state.get("autonomous_proposals") or []),
            evidence=[
                InvestigationEvidence.model_validate(item)
                for item in state.get("autonomous_evidence") or []
            ],
            findings=list(state.get("autonomous_grounded_findings") or []),
            trajectory=list(state.get("autonomous_trajectory") or []),
            critic_review=(
                CriticReviewOutput.model_validate(state["autonomous_critic_review"])
                if state.get("autonomous_critic_review")
                else None
            ),
            supervisor_decision=(
                SupervisorDecision.model_validate(state["autonomous_supervisor_decision"])
                if state.get("autonomous_supervisor_decision")
                else None
            ),
            budget=AutonomousBudget.model_validate(budget_payload) if budget_payload else None,
            budget_usage=usage,
        )

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
            "autonomous_investigation": (
                self._autonomous_summary(state).model_dump(mode="json")
                if self._autonomous_summary(state)
                else None
            ),
        }
        feedback = interrupt(payload)
        decision = str(feedback.get("decision", "reject"))
        comment = feedback.get("comment")
        await self._audit(state, "human_review_received", feedback)
        update: AgentState = {"approval_status": decision, "feedback_comment": comment}
        if state.get("autonomous_enabled"):
            proposal_id = str(state.get("autonomous_current_proposal_id") or "")
            proposal_status = {
                ApprovalDecision.APPROVE.value: "approved",
                ApprovalDecision.REQUEST_CHANGES.value: "awaiting_hitl",
            }.get(decision, "rejected")
            proposals = []
            for item in list(state.get("autonomous_proposals") or []):
                revised = dict(item)
                if revised.get("proposal_id") == proposal_id:
                    revised.update(
                        {
                            "status": proposal_status,
                            "sql": state.get("generated_sql") or revised.get("sql") or "",
                            "interpretation": state.get("interpretation") or "",
                            "selected_metrics": list(state.get("selected_metrics") or []),
                            "selected_dimensions": list(state.get("selected_dimensions") or []),
                            "selected_filters": list(state.get("selected_filters") or []),
                            "time_window": state.get("time_window"),
                            "source_objects": list(state.get("source_objects") or []),
                            "security_validation": dict(state.get("security_validation") or {}),
                            "cost_validation": dict(state.get("cost_validation") or {}),
                            "block_reason": comment if proposal_status == "rejected" else None,
                        }
                    )
                proposals.append(revised)
            update["autonomous_proposals"] = proposals
            action = {
                ApprovalDecision.APPROVE.value: "human_approved",
                ApprovalDecision.REQUEST_CHANGES.value: "human_requested_changes",
            }.get(decision, "human_rejected")
            trajectory, sequence = self._append_trajectory(
                state,
                stage="hitl",
                actor="human",
                action=action,
                task_id=str(state.get("autonomous_current_task_id") or "") or None,
                metadata={"revision": state.get("review_revision", 1)},
            )
            update["autonomous_trajectory"] = trajectory
            update["autonomous_trajectory_sequence"] = sequence
        if decision == ApprovalDecision.REQUEST_CHANGES.value:
            update["previous_review_sql"] = state.get("generated_sql", "")
        return update

    async def validate_security(self, state: AgentState) -> AgentState:
        domain = str(state["domain"])
        validation = self.validator.validate(
            state["generated_sql"],
            allowed_sources=self.catalog.allowed_sources(domain),
            policy=self.catalog.policies(domain),
            source_contracts=(state.get("semantic_context") or {}).get("source_contracts")
            or self.catalog.source_contracts(domain),
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
        if state.get("autonomous_enabled"):
            self.investigation_governance.assert_query_budget(
                int(state.get("autonomous_queries_executed") or 0)
            )
        result = await self.query_tool.execute(state["generated_sql"])
        executed_queries = int(state.get("autonomous_queries_executed") or 0)
        await self._audit(
            state,
            "sql_executed",
            {
                "row_count": result.row_count,
                "elapsed_ms": result.elapsed_ms,
                "truncated": result.truncated,
            },
        )
        update: AgentState = {"query_result": result.model_dump(mode="json")}
        if state.get("autonomous_enabled"):
            update["autonomous_queries_executed"] = executed_queries + 1
            trajectory, sequence = self._append_trajectory(
                state,
                stage="execution",
                actor="query_engine",
                action="sql_executed",
                task_id=str(state.get("autonomous_current_task_id") or "") or None,
                metadata={"row_count": result.row_count, "elapsed_ms": result.elapsed_ms},
            )
            update["autonomous_trajectory"] = trajectory
            update["autonomous_trajectory_sequence"] = sequence
        return update

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
    if state.get("intent") == "catalog_question":
        return "explore_semantics"
    if state.get("autonomous_available") and state.get("intent") == "analytical_query":
        return "initialize_society"
    if not state.get("domain") or state.get("domain_confidence", 0) < 0.70:
        return "clarification"
    return "explore_semantics"


def route_after_supervisor(state: AgentState) -> str:
    """Compatibility route used by tests; runtime uses WorkflowNodes.route_supervisor_dispatch."""
    if state.get("status") == "needs_clarification":
        return "clarification"
    if state.get("status") == "failed":
        return "end"
    action = (state.get("autonomous_supervisor_decision") or {}).get("action")
    if action in {SupervisorAction.FINALIZE.value, SupervisorAction.STOP_BUDGET.value}:
        return "synthesize_investigation"
    return "dispatch_specialist_wave"


def route_after_proposal_selection(state: AgentState) -> str:
    if state.get("autonomous_current_proposal_id"):
        return "estimate_llm_approval"
    if state.get("autonomous_mode") == InvestigationMode.DIRECT_SPECIALIST.value:
        return "direct_failure"
    return "critic_review"


def route_after_specialist_collection(state: AgentState) -> str:
    if state.get("autonomous_pending_proposals"):
        return "select_next_proposal"
    if state.get("autonomous_mode") == InvestigationMode.DIRECT_SPECIALIST.value:
        return "direct_failure"
    return "critic_review"


def route_after_evidence_recorded(state: AgentState) -> str:
    if state.get("autonomous_pending_proposals"):
        return "select_next_proposal"
    if state.get("autonomous_mode") == InvestigationMode.DIRECT_SPECIALIST.value:
        return "synthesize_direct_investigation"
    return "critic_review"


def route_after_autonomous_rejection(state: AgentState) -> str:
    if state.get("autonomous_pending_proposals"):
        return "select_next_proposal"
    if state.get("autonomous_mode") == InvestigationMode.DIRECT_SPECIALIST.value:
        return "rejected"
    return "critic_review"


def route_after_verification(state: AgentState) -> str:
    return "record_evidence" if state.get("autonomous_enabled") else "explain"


def route_after_review(state: AgentState) -> str:
    decision = state.get("approval_status")
    if decision == ApprovalDecision.APPROVE.value:
        return "execute_sql"
    if decision == ApprovalDecision.REQUEST_CHANGES.value:
        return "interpret_feedback"
    return "reject_autonomous_proposal" if state.get("autonomous_enabled") else "rejected"


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
