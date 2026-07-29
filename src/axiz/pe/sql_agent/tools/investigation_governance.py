from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axiz.pe.sql_agent.models.contracts import (
    AutonomousBudget,
    AutonomousBudgetUsage,
    InvestigationPlan,
    InvestigationQueryMode,
    InvestigationTask,
    InvestigationTaskStatus,
    SupervisorAction,
    SupervisorDecision,
)


class InvestigationGovernanceError(ValueError):
    pass


@dataclass(frozen=True)
class GovernedPlanResult:
    plan: InvestigationPlan
    warnings: list[str]


class InvestigationGovernancePolicy:
    """Deterministic authority boundary around the autonomous society.

    LLM agents may propose plans, delegation and evidence requests. They cannot increase budgets,
    enable a specialist, bypass HITL/security/cost, or execute SQL through another path.
    """

    def __init__(self, budget: AutonomousBudget) -> None:
        self.budget = budget

    @staticmethod
    def _role(value: object) -> str:
        return str(getattr(value, "value", value))

    def _validate_task(
        self,
        task: InvestigationTask,
        *,
        known_ids: set[str],
        enabled_roles: set[str],
        allow_previous_sql_revision: bool,
    ) -> None:
        role = self._role(task.specialist)
        if task.query_mode == InvestigationQueryMode.REVISE_PREVIOUS and not allow_previous_sql_revision:
            raise InvestigationGovernanceError(
                "Una solicitud independiente no puede reutilizar un SQL anterior"
            )
        if role == "critic":
            raise InvestigationGovernanceError(
                "El crítico es una etapa de revisión y no una tarea SQL delegable"
            )
        if role not in enabled_roles:
            raise InvestigationGovernanceError(
                f"El especialista {role!r} no tiene contratos semánticos habilitados"
            )
        unknown_dependencies = set(task.dependencies) - known_ids
        if unknown_dependencies:
            raise InvestigationGovernanceError(
                f"La tarea {task.task_id} depende de tareas inexistentes: "
                + ", ".join(sorted(unknown_dependencies))
            )
        if task.task_id in task.dependencies:
            raise InvestigationGovernanceError(
                f"La tarea {task.task_id} no puede depender de sí misma"
            )
        if task.attempts > task.task_budget.max_attempts:
            raise InvestigationGovernanceError(
                f"La tarea {task.task_id} excede su máximo de intentos"
            )
        if task.replans > task.task_budget.max_replans:
            raise InvestigationGovernanceError(
                f"La tarea {task.task_id} excede su máximo de replanificaciones"
            )

    def validate_plan(
        self,
        plan: InvestigationPlan,
        *,
        enabled_roles: set[str],
        allow_previous_sql_revision: bool = False,
    ) -> GovernedPlanResult:
        if not plan.tasks:
            raise InvestigationGovernanceError("El plan autónomo debe contener al menos una tarea")
        if len(plan.tasks) > self.budget.max_tasks:
            raise InvestigationGovernanceError(
                f"El plan contiene {len(plan.tasks)} tareas y supera el máximo de "
                f"{self.budget.max_tasks}"
            )
        ids = [task.task_id for task in plan.tasks]
        if len(ids) != len(set(ids)):
            raise InvestigationGovernanceError("Los task_id del plan deben ser únicos")
        known = set(ids)
        for task in plan.tasks:
            self._validate_task(
                task,
                known_ids=known,
                enabled_roles=enabled_roles,
                allow_previous_sql_revision=allow_previous_sql_revision,
            )
        self._validate_acyclic(plan.tasks)
        normalized = plan.model_copy(
            update={
                "tasks": [
                    task.model_copy(update={"status": InvestigationTaskStatus.PENDING})
                    for task in plan.tasks
                ]
            }
        )
        return GovernedPlanResult(plan=normalized, warnings=[])

    @staticmethod
    def _validate_acyclic(tasks: list[InvestigationTask]) -> None:
        graph = {task.task_id: set(task.dependencies) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise InvestigationGovernanceError("El plan contiene dependencias cíclicas")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

    def validate_supervisor_decision(
        self,
        decision: SupervisorDecision,
        *,
        plan: InvestigationPlan,
        usage: AutonomousBudgetUsage,
        enabled_roles: set[str],
        allow_previous_sql_revision: bool = False,
    ) -> SupervisorDecision:
        tasks = {task.task_id: task for task in plan.tasks}
        if decision.action == SupervisorAction.FINALIZE and usage.queries_executed < 1:
            raise InvestigationGovernanceError(
                "El supervisor no puede finalizar sin evidencia SQL verificada"
            )
        if decision.action == SupervisorAction.REJECT_CONCLUSION and not decision.rejected_conclusions:
            raise InvestigationGovernanceError(
                "Una decisión de rechazo debe identificar al menos una conclusión"
            )

        new_tasks = list(decision.new_tasks)
        if new_tasks:
            projected = len(tasks) + len(new_tasks)
            if projected > self.budget.max_tasks:
                raise InvestigationGovernanceError(
                    "El supervisor intentó superar el presupuesto máximo de tareas"
                )
            new_ids = [task.task_id for task in new_tasks]
            if len(new_ids) != len(set(new_ids)):
                raise InvestigationGovernanceError(
                    "El supervisor creó task_id duplicados en la misma decisión"
                )
            combined_ids = set(tasks) | set(new_ids)
            for task in new_tasks:
                if task.task_id in tasks:
                    raise InvestigationGovernanceError(
                        f"El supervisor creó un task_id duplicado: {task.task_id}"
                    )
                self._validate_task(
                    task,
                    known_ids=combined_ids,
                    enabled_roles=enabled_roles,
                    allow_previous_sql_revision=allow_previous_sql_revision,
                )
            self._validate_acyclic(list(plan.tasks) + new_tasks)

        projected_tasks = tasks | {task.task_id: task for task in new_tasks}
        selected = list(dict.fromkeys(decision.next_task_ids))
        if decision.next_task_id and decision.next_task_id not in selected:
            selected.append(decision.next_task_id)
        requires_delegation = decision.action in {
            SupervisorAction.DELEGATE,
            SupervisorAction.REQUEST_MORE_EVIDENCE,
        } or (
            decision.action == SupervisorAction.REJECT_CONCLUSION and bool(selected)
        )
        if requires_delegation and not selected:
            raise InvestigationGovernanceError(
                "La decisión de delegación debe seleccionar al menos una tarea"
            )
        if len(selected) > self.budget.max_parallel_tasks:
            raise InvestigationGovernanceError(
                f"El supervisor seleccionó {len(selected)} tareas y supera el máximo paralelo "
                f"de {self.budget.max_parallel_tasks}"
            )
        completed = {
            task.task_id
            for task in plan.tasks
            if task.status == InvestigationTaskStatus.COMPLETED
        }
        for task_id in selected:
            if task_id not in projected_tasks:
                raise InvestigationGovernanceError(
                    f"La decisión seleccionó una tarea inexistente: {task_id}"
                )
            candidate = projected_tasks[task_id]
            if candidate.status not in {
                InvestigationTaskStatus.PENDING,
                InvestigationTaskStatus.BLOCKED,
            }:
                raise InvestigationGovernanceError(
                    f"La tarea {task_id} no está disponible para delegación"
                )
            if not set(candidate.dependencies).issubset(completed):
                raise InvestigationGovernanceError(
                    f"La tarea {task_id} tiene dependencias pendientes"
                )
            if candidate.attempts >= candidate.task_budget.max_attempts:
                raise InvestigationGovernanceError(
                    f"La tarea {task_id} agotó sus intentos"
                )

        exhaustion = set(usage.exhausted_reasons)
        hard = {
            "max_iterations",
            "max_queries",
            "max_llm_tokens",
            "max_active_execution_seconds",
            "max_total_plan_cost",
            "max_total_plan_rows",
            "max_total_relation_bytes",
            "max_total_database_seconds",
        }
        if exhaustion & hard:
            return decision.model_copy(
                update={
                    "action": SupervisorAction.STOP_BUDGET,
                    "next_task_id": None,
                    "next_task_ids": [],
                }
            )
        return decision.model_copy(update={"next_task_ids": selected})

    def usage(self, state: dict[str, Any], *, llm_tokens: int) -> AutonomousBudgetUsage:
        previous = AutonomousBudgetUsage.model_validate(
            state.get("autonomous_budget_usage") or {}
        )
        iterations = int(state.get("autonomous_iteration") or previous.iterations)
        plan = state.get("autonomous_plan") or {}
        tasks_created = len(plan.get("tasks") or [])
        queries = int(state.get("autonomous_queries_executed") or previous.queries_executed)
        exhausted: list[str] = []
        checks = (
            (iterations >= self.budget.max_iterations, "max_iterations"),
            (tasks_created >= self.budget.max_tasks, "max_tasks"),
            (queries >= self.budget.max_queries, "max_queries"),
            (llm_tokens >= self.budget.max_llm_tokens, "max_llm_tokens"),
            (
                previous.active_execution_seconds >= self.budget.max_active_execution_seconds,
                "max_active_execution_seconds",
            ),
            (previous.total_plan_cost >= self.budget.max_total_plan_cost, "max_total_plan_cost"),
            (previous.total_plan_rows >= self.budget.max_total_plan_rows, "max_total_plan_rows"),
            (
                previous.total_relation_bytes >= self.budget.max_total_relation_bytes,
                "max_total_relation_bytes",
            ),
            (
                previous.total_database_seconds >= self.budget.max_total_database_seconds,
                "max_total_database_seconds",
            ),
        )
        for condition, name in checks:
            if condition:
                exhausted.append(name)
        return previous.model_copy(
            update={
                "iterations": iterations,
                "tasks_created": tasks_created,
                "queries_executed": queries,
                "llm_tokens": llm_tokens,
                "exhausted_reasons": exhausted,
            }
        )


    def proposal_budget_violations(
        self,
        usage: AutonomousBudgetUsage,
        cost: Any,
    ) -> list[str]:
        """Evaluate a proposed query against cumulative investigation budgets.

        This reserves no authority and performs no mutation. It is called again for each proposal
        after prior HITL decisions and executions have updated cumulative usage.
        """
        violations: list[str] = []
        if usage.queries_executed + 1 > self.budget.max_queries:
            violations.append("max_queries")
        projected_cost = usage.total_plan_cost + float(
            getattr(cost, "total_cost", 0.0) or 0.0
        )
        if projected_cost > self.budget.max_total_plan_cost:
            violations.append("max_total_plan_cost")
        projected_rows = int(
            getattr(cost, "max_node_rows", 0)
            or getattr(cost, "plan_rows", 0)
            or 0
        )
        if usage.total_plan_rows + projected_rows > self.budget.max_total_plan_rows:
            violations.append("max_total_plan_rows")
        projected_relation_bytes = usage.total_relation_bytes + int(
            getattr(cost, "relation_bytes", 0) or 0
        )
        if projected_relation_bytes > self.budget.max_total_relation_bytes:
            violations.append("max_total_relation_bytes")
        if usage.total_database_seconds >= self.budget.max_total_database_seconds:
            violations.append("max_total_database_seconds")
        if usage.active_execution_seconds >= self.budget.max_active_execution_seconds:
            violations.append("max_active_execution_seconds")
        return violations

    def assert_query_budget(self, executed_queries: int) -> None:
        if executed_queries >= self.budget.max_queries:
            raise InvestigationGovernanceError(
                f"Se alcanzó el máximo de {self.budget.max_queries} consultas por investigación"
            )
