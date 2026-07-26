from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from html import escape
from typing import Any, Iterable

import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import ApiClient

st.set_page_config(
    page_title="Axiz SQL Agent",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      [data-testid="stSidebar"] { min-width: 300px; max-width: 300px; }
      [data-testid="stSidebar"] .stButton > button { text-align: left; border-radius: 9px; }
      [data-testid="stSidebar"] [data-testid="stPopover"] button {
          min-width: 2.35rem; padding-left: .45rem; padding-right: .45rem;
      }
      .sidebar-brand { font-size: 1.05rem; font-weight: 650; margin-bottom: .4rem; }
      .session-group { color: #6b7280; font-size: .75rem; font-weight: 650;
                       margin: .8rem 0 .15rem; text-transform: uppercase; }
      .session-caption { color: #8b8f98; font-size: .70rem; margin: -.45rem 0 .25rem .4rem; }
      .current-session { color: #6b7280; font-size: .82rem; }
      .trace-step { border-left: 2px solid rgba(128,128,128,.28); padding-left: .8rem;
                    margin: .35rem 0 .8rem; }
      .trace-detail { color: #6b7280; font-size: .86rem; }
      .review-card { border: 1px solid rgba(128,128,128,.28); border-radius: 12px;
                     padding: .8rem 1rem; margin: .25rem 0 .75rem 0; }
      .model-usage-line { color: #6b7280; font-size: .78rem; line-height: 1.25rem;
                          margin: .25rem 0 .55rem; white-space: nowrap; overflow: hidden;
                          text-overflow: ellipsis; }
      .model-usage-line strong { color: inherit; font-weight: 600; }
      .block-container { max-width: 1080px; padding-top: 1.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

for key, default in {
    "token": None,
    "sessions": [],
    "session_id": None,
    "messages": [],
    "pending_run": None,
    "feedback_action": None,
    "transient_agent_error": None,
    "show_agent_trace": True,
}.items():
    st.session_state.setdefault(key, default)


def render_trace_details(trace: list[dict[str, Any]] | None) -> None:
    if not trace or not st.session_state.show_agent_trace:
        st.caption("La actividad detallada del agente está oculta desde la barra lateral.")
        return
    st.caption(
        "Resumen auditable de decisiones, herramientas y validaciones. "
        "No expone razonamiento privado ni tokens internos del modelo."
    )
    for step in trace:
        st.markdown(f"**{step.get('label', step.get('stage', 'Etapa'))}**")
        if step.get("detail"):
            st.markdown(
                f"<div class='trace-detail'>{step['detail']}</div>",
                unsafe_allow_html=True,
            )
        summary = step.get("summary") or {}
        if summary:
            with st.container(border=True):
                for key, value in summary.items():
                    label = key.replace("_", " ").capitalize()
                    if isinstance(value, list):
                        rendered = ", ".join(str(item) for item in value) or "—"
                    elif isinstance(value, bool):
                        rendered = "Sí" if value else "No"
                    else:
                        rendered = str(value)
                    st.markdown(f"**{label}:** {rendered}")


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if abs(size) < 1024 or candidate == units[-1]:
            break
        size /= 1024
    return f"{size:,.2f} {unit}" if unit != "B" else f"{int(size):,} B"


def format_number(value: int | float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.{decimals}f}"


def _explain_root(explain_plan: Any) -> dict[str, Any] | None:
    if isinstance(explain_plan, list) and explain_plan:
        first = explain_plan[0]
        if isinstance(first, dict):
            plan = first.get("Plan")
            return plan if isinstance(plan, dict) else None
    if isinstance(explain_plan, dict):
        plan = explain_plan.get("Plan")
        if isinstance(plan, dict):
            return plan
        if explain_plan.get("Node Type"):
            return explain_plan
    return None


def flatten_explain_plan(explain_plan: Any) -> list[dict[str, Any]]:
    root = _explain_root(explain_plan)
    if not root:
        return []

    rows: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], depth: int, path: str) -> None:
        schema = node.get("Schema")
        relation_name = node.get("Relation Name")
        relation = (
            f"{schema}.{relation_name}"
            if schema and relation_name
            else relation_name or "—"
        )
        condition = (
            node.get("Index Cond")
            or node.get("Hash Cond")
            or node.get("Merge Cond")
            or node.get("Join Filter")
            or node.get("Filter")
            or "—"
        )
        rows.append(
            {
                "Paso": path,
                "Operación": f"{'  ' * depth}{node.get('Node Type', '—')}",
                "Relación": relation,
                "Método": node.get("Join Type") or node.get("Scan Direction") or "—",
                "Filas estimadas": int(node.get("Plan Rows", 0) or 0),
                "Ancho fila (B)": int(node.get("Plan Width", 0) or 0),
                "Costo inicial": float(node.get("Startup Cost", 0) or 0),
                "Costo total": float(node.get("Total Cost", 0) or 0),
                "Filtro / condición": str(condition),
            }
        )
        for index, child in enumerate(node.get("Plans", []) or [], start=1):
            if isinstance(child, dict):
                walk(child, depth + 1, f"{path}.{index}")

    walk(root, 0, "1")
    return rows


def render_validation_panel(payload: dict[str, Any]) -> None:
    security = payload.get("security_validation") or {}
    cost = payload.get("cost_validation") or {}
    has_security = bool(security)
    has_cost = bool(cost)
    if not has_security and not has_cost:
        return

    st.markdown("#### Validación previa a la aprobación y ejecución")
    security_column, cost_column = st.columns(2)

    with security_column:
        approved = bool(security.get("approved"))
        if not has_security:
            st.info("Seguridad no evaluada", icon="ℹ️")
        elif approved:
            st.success("Seguridad aprobada", icon="✅")
        else:
            st.error("Seguridad rechazada", icon="⛔")
        st.metric("Tipo de sentencia", security.get("statement_type") or "—")
        st.metric(
            "Límite aplicado",
            format_number(security.get("enforced_limit") or security.get("max_rows"), 0),
            help="Máximo de filas que la consulta puede devolver.",
        )

    with cost_column:
        approved = bool(cost.get("approved"))
        if not has_cost:
            st.info("Costo no evaluado", icon="ℹ️")
        elif approved:
            st.success("Costo dentro de límites", icon="✅")
        else:
            st.error("Costo rechazado", icon="⛔")
        st.metric(
            "Costo del planner",
            format_number(cost.get("total_cost")),
            help=f"Límite configurado: {format_number(cost.get('max_plan_cost'))}",
        )
        st.metric(
            "Máximo de filas por nodo",
            format_number(cost.get("max_node_rows") or cost.get("plan_rows"), 0),
            help=(
                "Mayor cantidad de filas que PostgreSQL estima procesar en un paso del plan. "
                f"Límite: {format_number(cost.get('max_plan_rows'), 0)}."
            ),
        )

    security_tab, cost_tab, plan_tab = st.tabs(
        ["Controles de seguridad", "Evaluación de costo", "Plan de ejecución"]
    )
    with security_tab:
        if not has_security:
            st.caption("La validación de seguridad no se ejecutó para esta respuesta.")
        sources = security.get("tables") or []
        columns = security.get("columns") or []
        st.markdown(f"**Fuentes autorizadas usadas:** {len(sources)}")
        if sources:
            st.code("\n".join(str(item) for item in sources), language="text")
        st.markdown(f"**Columnas detectadas:** {len(columns)}")
        if columns:
            st.caption(", ".join(str(item) for item in columns))

        required_filters = security.get("required_filter_columns") or []
        denied_schemas = security.get("denied_schemas") or []
        denied_functions = security.get("denied_functions") or []
        st.markdown(
            "**Reglas aplicadas:** una sola sentencia, solo lectura, fuentes en allowlist, "
            "sin DDL/DML y sin joins cartesianos."
        )
        if required_filters:
            st.markdown("**Filtro temporal requerido:** " + ", ".join(required_filters))
        if denied_schemas:
            st.markdown("**Esquemas bloqueados:** " + ", ".join(denied_schemas))
        if denied_functions:
            st.markdown("**Funciones bloqueadas:** " + ", ".join(denied_functions))
        violations = security.get("violations") or []
        if violations:
            st.error("La consulta incumplió estas reglas:")
            for violation in violations:
                st.markdown(f"- {violation}")
        elif has_security:
            st.caption("No se detectaron violaciones de seguridad.")

    with cost_tab:
        if not has_cost:
            st.caption("La validación de costo no se ejecutó para esta respuesta.")
        metrics = [
            {
                "Métrica": "Costo del planner",
                "Valor": format_number(cost.get("total_cost")),
                "Límite": format_number(cost.get("max_plan_cost")),
            },
            {
                "Métrica": "Filas estimadas de salida",
                "Valor": format_number(cost.get("plan_rows"), 0),
                "Límite": format_number(security.get("enforced_limit"), 0),
            },
            {
                "Métrica": "Máximo de filas procesadas por un nodo",
                "Valor": format_number(cost.get("max_node_rows"), 0),
                "Límite": format_number(cost.get("max_plan_rows"), 0),
            },
            {
                "Métrica": "Nodos del plan",
                "Valor": format_number(cost.get("plan_node_count"), 0),
                "Límite": "Informativo",
            },
            {
                "Métrica": "Tamaño de relaciones",
                "Valor": format_bytes(cost.get("relation_bytes")),
                "Límite": format_bytes(cost.get("max_relation_bytes")),
            },
            {
                "Métrica": "Timeout",
                "Valor": f"{cost.get('timeout_seconds')} s" if cost.get("timeout_seconds") else "—",
                "Límite": "Configuración de ejecución",
            },
        ]
        st.dataframe(pd.DataFrame(metrics), hide_index=True, width="stretch")
        semantic_tables = cost.get("tables") or []
        plan_relations = cost.get("plan_relations") or []
        if semantic_tables:
            st.markdown("**Fuentes semánticas recibidas:**")
            st.code("\n".join(str(item) for item in semantic_tables), language="text")
        if plan_relations:
            st.markdown("**Relaciones físicas detectadas en el plan:**")
            st.code("\n".join(str(item) for item in plan_relations), language="text")
        warnings = cost.get("warnings") or []
        if warnings:
            st.warning("La política de costo generó advertencias:")
            for warning in warnings:
                st.markdown(f"- {warning}")
        elif has_cost:
            st.caption("El plan quedó dentro de todos los límites configurados.")

    with plan_tab:
        explain_plan = cost.get("explain_plan")
        plan_rows = flatten_explain_plan(explain_plan)
        st.caption(
            "Muestra exclusivamente cómo PostgreSQL planea ejecutar el SQL. "
            "No contiene las filas de negocio devueltas por la consulta."
        )
        if plan_rows:
            st.dataframe(
                pd.DataFrame(plan_rows),
                hide_index=True,
                width="stretch",
                column_config={
                    "Paso": st.column_config.TextColumn(width="small"),
                    "Operación": st.column_config.TextColumn(width="medium"),
                    "Relación": st.column_config.TextColumn(width="medium"),
                    "Filtro / condición": st.column_config.TextColumn(width="large"),
                    "Costo inicial": st.column_config.NumberColumn(format="%.2f"),
                    "Costo total": st.column_config.NumberColumn(format="%.2f"),
                },
            )
            st.caption("JSON técnico de EXPLAIN")
            st.json(explain_plan, expanded=False)
        else:
            st.caption("No hay un plan de ejecución disponible para esta respuesta.")


def _models_used(usage: dict[str, Any] | None) -> list[str]:
    entries: list[tuple[str, str]] = []
    for call in (usage or {}).get("calls") or []:
        provider = str(call.get("provider") or "").strip()
        model = str(call.get("model") or "").strip()
        if model and (provider, model) not in entries:
            entries.append((provider, model))
    providers = {provider for provider, _ in entries if provider}
    if len(providers) <= 1:
        return [model for _, model in entries]
    return [f"{provider}/{model}" if provider else model for provider, model in entries]


def render_compact_model_usage(
    usage: dict[str, Any] | None,
    estimate: dict[str, Any] | None = None,
) -> None:
    models = _models_used(usage)
    total_tokens = int((usage or {}).get("actual_total_tokens") or 0)
    calls = int((usage or {}).get("call_count") or 0)
    has_estimate = bool(estimate and estimate.get("expected_call_count"))
    if not models and not calls and not has_estimate:
        return

    segments = ["🤖 " + (", ".join(models) if models else "modelo pendiente")]
    if calls:
        segments.append(f"{format_number(total_tokens, 0)} tokens usados")
        segments.append(f"{calls} {'llamada' if calls == 1 else 'llamadas'}")
    if estimate and estimate.get("expected_call_count"):
        estimated = format_number(estimate.get("estimated_total_tokens"), 0)
        future_calls = int(estimate.get("expected_call_count") or 0)
        segments.append(
            f"+~{estimated} al aprobar ({future_calls} "
            f"{'llamada' if future_calls == 1 else 'llamadas'})"
        )
    text = " · ".join(segments)
    st.markdown(
        f'<div class="model-usage-line" title="{escape(text, quote=True)}">'
        f"{escape(text)}</div>",
        unsafe_allow_html=True,
    )


def render_llm_usage_details(usage: dict[str, Any] | None) -> None:
    if not usage or not usage.get("call_count"):
        st.caption("No hay consumo LLM registrado para esta respuesta.")
        return

    calls, input_col, output_col, total_col = st.columns(4)
    calls.metric("Llamadas ejecutadas", format_number(usage.get("call_count"), 0))
    input_col.metric("Entrada consumida", format_number(usage.get("actual_input_tokens"), 0))
    output_col.metric("Salida consumida", format_number(usage.get("actual_output_tokens"), 0))
    total_col.metric("Total consumido", format_number(usage.get("actual_total_tokens"), 0))

    if usage.get("cached_input_tokens"):
        st.caption(
            "Tokens de entrada cacheados: "
            + format_number(usage.get("cached_input_tokens"), 0)
        )
    if usage.get("reasoning_output_tokens"):
        st.caption(
            "Tokens de razonamiento reportados: "
            + format_number(usage.get("reasoning_output_tokens"), 0)
        )
    if not usage.get("actual_usage_complete", True):
        st.warning(
            "Alguna llamada no devolvió métricas reales; los totales consumidos pueden ser parciales."
        )

    rows: list[dict[str, Any]] = []
    for call in usage.get("calls") or []:
        rows.append(
            {
                "Agente": call.get("agent"),
                "Proveedor": call.get("provider"),
                "Modelo": call.get("model"),
                "Estado": call.get("status"),
                "Entrada consumida": call.get("input_tokens"),
                "Salida consumida": call.get("output_tokens"),
                "Total consumido": call.get("total_tokens"),
                "Entrada estimada antes de llamar": call.get("estimated_input_tokens"),
                "Salida máxima configurada": call.get("reserved_output_tokens"),
                "Cacheados": call.get("cached_input_tokens", 0),
                "Razonamiento": call.get("reasoning_output_tokens", 0),
                "Duración ms": round(float(call.get("duration_ms") or 0), 2),
                "Intentos": call.get("attempt_count", 1),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(
        "Los campos consumidos son reales. La salida máxima configurada es solo el límite de "
        "generación de cada llamada."
    )


def render_approval_llm_estimate_details(
    estimate: dict[str, Any] | None,
    usage: dict[str, Any] | None,
) -> None:
    if not estimate or not estimate.get("expected_call_count"):
        st.caption("No hay llamadas LLM posteriores estimadas para esta respuesta.")
        return

    calls_col, input_col, output_col, total_col, projected_col = st.columns(5)
    calls_col.metric("Llamadas previstas", format_number(estimate.get("expected_call_count"), 0))
    input_col.metric("Entrada estimada", format_number(estimate.get("estimated_input_tokens"), 0))
    output_col.metric("Salida estimada", format_number(estimate.get("estimated_output_tokens"), 0))
    total_col.metric(
        "Consumo adicional estimado",
        format_number(estimate.get("estimated_total_tokens"), 0),
        help=(
            "Estimación probable. El máximo configurado para estas llamadas es "
            f"{format_number(estimate.get('maximum_total_tokens'), 0)} tokens."
        ),
    )
    current_actual = int((usage or {}).get("actual_total_tokens") or 0)
    projected_col.metric(
        "Total proyectado del run",
        format_number(current_actual + int(estimate.get("estimated_total_tokens") or 0), 0),
    )
    st.caption(
        "Base de cálculo: "
        f"{format_number(estimate.get('projected_result_rows'), 0)} filas de salida y "
        f"{format_number(estimate.get('projected_row_width_bytes'), 0)} bytes por fila."
    )

    rows: list[dict[str, Any]] = []
    for call in estimate.get("calls") or []:
        rows.append(
            {
                "Agente": call.get("agent"),
                "Proveedor": call.get("provider"),
                "Modelo": call.get("model"),
                "Entrada estimada": call.get("estimated_input_tokens"),
                "Salida estimada": call.get("estimated_output_tokens"),
                "Total estimado": call.get("estimated_total_tokens"),
                "Salida máxima": call.get("max_output_tokens"),
                "Máximo total": call.get("maximum_total_tokens"),
                "Base": call.get("basis"),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for assumption in estimate.get("assumptions") or []:
        st.markdown(f"- {assumption}")


def _interpretation_from_payload(payload: dict[str, Any]) -> str | None:
    interpretation = payload.get("interpretation")
    if interpretation:
        return str(interpretation)
    review = payload.get("review") or {}
    if review.get("interpretation"):
        return str(review["interpretation"])
    for step in payload.get("trace") or []:
        if step.get("stage") == "generate_sql":
            summary = step.get("summary") or {}
            if summary.get("interpretation"):
                return str(summary["interpretation"])
    return None


def _source_objects(payload: dict[str, Any]) -> list[str]:
    sources = payload.get("source_objects") or []
    if not sources:
        sources = (payload.get("security_validation") or {}).get("tables") or []
    return [str(source) for source in sources]


def render_query_explanation(
    *,
    interpretation: str | None,
    sources: list[str],
    assumptions: list[str],
    max_rows: int | None,
) -> None:
    with st.expander("Qué hace esta consulta", expanded=False):
        if interpretation:
            st.markdown(
                "La consulta traduce la interpretación mostrada arriba a una operación SQL "
                "de solo lectura sobre la capa semántica gobernada."
            )
        else:
            st.markdown(
                "La consulta usa exclusivamente objetos semánticos autorizados y se ejecuta "
                "con una conexión de solo lectura."
            )
        if sources:
            st.markdown("**Fuentes semánticas:** " + ", ".join(sources))
        if max_rows:
            st.markdown(f"**Máximo de filas devueltas:** {format_number(max_rows, 0)}")
        if assumptions:
            st.markdown("**Supuestos:**")
            for assumption in assumptions:
                st.markdown(f"- {assumption}")


def render_feedback_compliance(payload: dict[str, Any]) -> None:
    plan = payload.get("feedback_plan") or {}
    compliance = payload.get("feedback_compliance") or {}
    application = payload.get("feedback_application") or {}
    if not plan:
        st.caption("Esta revisión no proviene de una solicitud de cambios HITL.")
        return

    if compliance.get("compliant"):
        st.success("La revisión cumple todos los cambios solicitados.")
    else:
        st.warning("La revisión todavía no cumple todos los cambios solicitados.")

    st.markdown(f"**Plan:** {plan.get('summary') or 'Sin resumen'}")
    st.caption(f"Estrategia híbrida: {plan.get('strategy') or 'no especificada'}")
    rows: list[dict[str, Any]] = []
    check_by_id = {
        item.get("change_id"): item for item in compliance.get("checks") or []
    }
    for change in plan.get("changes") or []:
        change_id = change.get("change_id")
        check = check_by_id.get(change_id) or {}
        rows.append(
            {
                "Cambio": change_id,
                "Tipo": change.get("change_type"),
                "Objetivo": change.get("target") or change.get("value") or change.get("limit"),
                "AST": (
                    "Aplicado"
                    if change_id in (application.get("applied_changes") or [])
                    else "Verificado"
                    if check.get("passed") is True
                    else "Pendiente"
                    if check.get("passed") is False
                    else "Semántico"
                ),
                "Cumple": check.get("passed"),
                "Evidencia": check.get("evidence"),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if compliance.get("missing_changes"):
        st.markdown("**Cambios faltantes:** " + ", ".join(compliance["missing_changes"]))
    if compliance.get("unexpected_changes"):
        st.markdown("**Cambios no solicitados:**")
        for item in compliance["unexpected_changes"]:
            st.markdown(f"- {item}")


def render_autonomous_investigation(payload: dict[str, Any] | None) -> None:
    investigation = payload or {}
    plan = investigation.get("plan") or {}
    tasks = plan.get("tasks") or []
    budget = investigation.get("budget") or {}
    usage = investigation.get("budget_usage") or {}

    if plan.get("objective"):
        st.markdown("#### Objetivo de investigación")
        st.markdown(str(plan["objective"]))
    if plan.get("strategy"):
        st.caption(str(plan["strategy"]))

    if budget:
        cols = st.columns(7)
        cols[0].metric(
            "Iteraciones",
            f"{usage.get('iterations', 0)} / {budget.get('max_iterations', '—')}",
        )
        cols[1].metric(
            "Tareas",
            f"{usage.get('tasks_created', len(tasks))} / {budget.get('max_tasks', '—')}",
        )
        cols[2].metric(
            "Consultas",
            f"{usage.get('queries_executed', 0)} / {budget.get('max_queries', '—')}",
        )
        cols[3].metric(
            "Tokens LLM",
            f"{format_number(usage.get('llm_tokens'), 0)} / "
            f"{format_number(budget.get('max_llm_tokens'), 0)}",
        )
        cols[4].metric(
            "Tiempo activo",
            f"{float(usage.get('active_execution_seconds') or 0):.1f} / "
            f"{budget.get('max_active_execution_seconds', '—')} s",
        )
        cols[5].metric(
            "Olas paralelas",
            str(usage.get("parallel_waves", 0)),
        )
        cols[6].metric(
            "Cache hits",
            str(usage.get("cache_hits", 0)),
        )
        st.caption(
            "Presupuesto acumulado BD: "
            f"costo {format_number(usage.get('total_plan_cost'), 0)} / "
            f"{format_number(budget.get('max_total_plan_cost'), 0)} · "
            f"filas de plan {format_number(usage.get('total_plan_rows'), 0)} / "
            f"{format_number(budget.get('max_total_plan_rows'), 0)} · "
            f"tiempo SQL {float(usage.get('total_database_seconds') or 0):.1f} / "
            f"{budget.get('max_total_database_seconds', '—')} s"
        )

    if tasks:
        st.markdown("#### Tareas delegadas")
        rows = []
        for task in tasks:
            rows.append(
                {
                    "ID": task.get("task_id"),
                    "Especialista": task.get("specialist"),
                    "Objetivo": task.get("title") or task.get("objective"),
                    "Dominio": task.get("domain"),
                    "Estado": task.get("status"),
                    "Modo SQL": task.get("query_mode"),
                    "Dependencias": ", ".join(task.get("dependencies") or []),
                    "Ola": task.get("wave", 0),
                    "Intentos": task.get("attempts", 0),
                    "Replanes": task.get("replans", 0),
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    decision = investigation.get("supervisor_decision") or {}
    if decision:
        st.markdown("#### Última decisión del supervisor")
        st.markdown(f"**{decision.get('action', '—')}** — {decision.get('rationale', '')}")
        selected = decision.get("next_task_ids") or ([decision.get("next_task_id")] if decision.get("next_task_id") else [])
        if selected:
            st.caption("Tareas seleccionadas: " + ", ".join(str(item) for item in selected))

    proposals = investigation.get("proposals") or []
    if proposals:
        st.markdown("#### Propuestas de especialistas")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Propuesta": item.get("proposal_id"),
                        "Tarea": item.get("task_id"),
                        "Especialista": item.get("specialist_id"),
                        "Ola": item.get("wave"),
                        "Estado": item.get("status"),
                        "Cache": "sí" if item.get("cache_hit") else "no",
                        "Seguridad": (item.get("security_validation") or {}).get("approved"),
                        "Costo": (item.get("cost_validation") or {}).get("approved"),
                    }
                    for item in proposals
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    critic = investigation.get("critic_review") or {}
    if critic:
        st.markdown("#### Revisión crítica")
        st.caption(
            "Lista para finalizar: "
            + ("sí" if critic.get("ready_to_finalize") else "no")
            + f" · confianza {critic.get('confidence', '—')}"
        )
        for label, key in (
            ("Contradicciones", "contradictions"),
            ("Evidencia faltante", "missing_evidence"),
            ("Conclusiones rechazadas", "rejected_conclusions"),
        ):
            values = critic.get(key) or []
            if values:
                st.markdown(f"**{label}:**")
                for value in values:
                    st.markdown(f"- {value}")

    trajectory = investigation.get("trajectory") or []
    if trajectory:
        st.markdown("#### Trayectoria observable")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Secuencia": item.get("sequence"),
                        "Etapa": item.get("stage"),
                        "Actor": item.get("actor"),
                        "Acción": item.get("action"),
                        "Tarea": item.get("task_id"),
                        "Especialista": item.get("specialist_id"),
                        "Ola": item.get("wave"),
                        "Cache": item.get("cache_hit", False),
                    }
                    for item in trajectory
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    grounded_findings = investigation.get("findings") or []
    if grounded_findings:
        st.markdown("#### Hallazgos trazables")
        for finding in grounded_findings:
            references = ", ".join(finding.get("evidence_ids") or [])
            st.markdown(f"- {finding.get('statement', '')}  ")
            st.caption(
                f"Evidencia: {references or '—'} · confianza {finding.get('confidence', '—')}"
            )
            for limitation in finding.get("limitations") or []:
                st.caption("Limitación: " + str(limitation))

    evidence = investigation.get("evidence") or []
    if evidence:
        st.markdown("#### Evidencia acumulada")
        for item in evidence:
            title = (
                f"{item.get('evidence_id', 'evidencia')} · "
                f"{item.get('specialist', 'especialista')} · {item.get('task_id', '')}"
            )
            with st.expander(title, expanded=False):
                st.markdown(item.get("summary") or "Sin resumen")
                if item.get("findings"):
                    for finding in item["findings"]:
                        st.markdown(f"- {finding}")
                st.code(item.get("sql") or "", language="sql")

def render_advanced_details(
    payload: dict[str, Any],
    *,
    usage: dict[str, Any] | None,
    estimate: dict[str, Any] | None,
    trace: list[dict[str, Any]] | None,
    domain: str | None = None,
    revision: int | None = None,
    sources: list[str] | None = None,
    assumptions: list[str] | None = None,
) -> None:
    with st.expander("Detalles avanzados", expanded=False):
        if domain or revision:
            values = []
            if domain:
                values.append(f"Dominio: {domain}")
            if revision:
                values.append(f"Revisión: {revision}")
            st.caption(" · ".join(values))
        if sources:
            st.markdown("**Fuentes semánticas:**")
            st.code("\n".join(sources), language="text")
        if assumptions:
            st.markdown("**Supuestos utilizados:**")
            for assumption in assumptions:
                st.markdown(f"- {assumption}")

        tab_names = []
        if payload.get("autonomous_investigation"):
            tab_names.append("Investigación autónoma")
        tab_names.append("Seguridad, costo y plan")
        if payload.get("feedback_plan"):
            tab_names.append("Cumplimiento de cambios")
        tab_names.extend(["Consumo LLM", "Actividad del agente"])
        tabs = st.tabs(tab_names)
        cursor = 0
        if payload.get("autonomous_investigation"):
            with tabs[cursor]:
                render_autonomous_investigation(payload.get("autonomous_investigation"))
            cursor += 1
        with tabs[cursor]:
            render_validation_panel(payload)
        cursor += 1
        if payload.get("feedback_plan"):
            with tabs[cursor]:
                render_feedback_compliance(payload)
            cursor += 1
        usage_tab = tabs[cursor]
        activity_tab = tabs[cursor + 1]
        with usage_tab:
            st.markdown("#### Consumo LLM ejecutado")
            render_llm_usage_details(usage)
            if estimate and estimate.get("expected_call_count"):
                st.markdown("#### Estimación LLM si apruebas este SQL")
                render_approval_llm_estimate_details(estimate, usage)
        with activity_tab:
            render_trace_details(trace)


def _render_result_data(client: ApiClient, payload: dict[str, Any]) -> None:
    if payload.get("answer"):
        st.markdown(payload["answer"])
    if payload.get("key_findings"):
        st.markdown("**Hallazgos**")
        for finding in payload["key_findings"]:
            st.markdown(f"- {finding}")

    result = payload.get("result")
    if result and result.get("rows"):
        frame = pd.DataFrame(result["rows"])
        spec = payload.get("visualization") or {"type": "table"}
        chart_type = spec.get("type")
        x = spec.get("x")
        y = [column for column in spec.get("y", []) if column in frame.columns]
        if chart_type == "bar" and x in frame.columns and y:
            st.plotly_chart(px.bar(frame, x=x, y=y, title=spec.get("title")), width="stretch")
        elif chart_type == "line" and x in frame.columns and y:
            st.plotly_chart(px.line(frame, x=x, y=y, title=spec.get("title")), width="stretch")
        st.dataframe(frame, width="stretch", hide_index=True)
        st.caption(
            f"{result.get('row_count', len(frame))} filas · "
            f"{result.get('elapsed_ms', 0):.0f} ms"
        )

        export = payload.get("export") or {}
        run_id = str(payload.get("run_id") or "")
        if export.get("available") and run_id:
            def generate_excel() -> bytes:
                return client.download_excel(run_id)

            st.download_button(
                "Exportar Excel",
                data=generate_excel,
                file_name=f"resultado-sql-{run_id[:8]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"download-excel-{run_id}",
                on_click="ignore",
                width="content",
                icon=":material/download:",
                help=(
                    "Genera y descarga el XLSX en un solo clic, usando el resultado persistido; "
                    "no vuelve a ejecutar el SQL."
                ),
            )
        elif export.get("reason"):
            st.caption(f"Exportación Excel no disponible: {export['reason']}")

    if payload.get("caveats"):
        st.markdown("**Advertencias**")
        for caveat in payload["caveats"]:
            st.markdown(f"- {caveat}")


def render_result(
    client: ApiClient,
    payload: dict[str, Any],
    *,
    include_answer: bool = True,
) -> None:
    interpretation = _interpretation_from_payload(payload)
    sql = payload.get("sql")
    sources = _source_objects(payload)
    assumptions = [str(item) for item in payload.get("assumptions") or []]
    security = payload.get("security_validation") or {}
    usage = payload.get("llm_usage")
    has_result_content = bool(payload.get("answer") or (payload.get("result") or {}).get("rows"))

    if not sql:
        # Conversational, capability and catalog answers should read like chat responses.
        # They must not display SQL-specific explanations or empty validation panels.
        _render_result_data(client, payload)
        render_compact_model_usage(usage)
        if payload.get("trace") or usage:
            render_advanced_details(
                payload,
                usage=usage,
                estimate=None,
                trace=payload.get("trace"),
                domain=payload.get("domain"),
                sources=sources,
                assumptions=assumptions,
            )
        if payload.get("error"):
            st.error(payload["error"])
        return

    if interpretation:
        st.markdown("**Interpretación**")
        st.markdown(interpretation)
    render_compact_model_usage(usage)

    if has_result_content:
        with st.expander("Resultado y visualización", expanded=True):
            _render_result_data(client, payload)

    with st.expander("SQL ejecutado", expanded=False):
        st.code(sql, language="sql")

    render_query_explanation(
        interpretation=interpretation,
        sources=sources,
        assumptions=assumptions,
        max_rows=security.get("enforced_limit") or security.get("max_rows"),
    )
    render_advanced_details(
        payload,
        usage=usage,
        estimate=None,
        trace=payload.get("trace"),
        domain=payload.get("domain"),
        sources=sources,
        assumptions=assumptions,
    )
    if payload.get("error"):
        st.error(payload["error"])


def refresh_conversations(client: ApiClient, preferred_session_id: str | None = None) -> None:
    sessions = client.list_sessions()
    if not sessions:
        sessions = [client.create_session()]
    st.session_state.sessions = sessions
    available = {str(item["id"]) for item in sessions}
    selected = preferred_session_id or st.session_state.session_id
    if selected not in available:
        selected = str(sessions[0]["id"])
    load_conversation(client, selected)


def load_conversation(client: ApiClient, session_id: str) -> None:
    st.session_state.session_id = session_id
    st.session_state.messages = client.list_messages(session_id)
    selected = next(
        (item for item in st.session_state.sessions if str(item["id"]) == session_id),
        None,
    )
    pending_run_id = selected.get("pending_run_id") if selected else None
    st.session_state.pending_run = (
        client.get_run(str(pending_run_id)) if pending_run_id else None
    )
    st.session_state.feedback_action = None


def current_session() -> dict[str, Any] | None:
    return next(
        (
            item
            for item in st.session_state.sessions
            if str(item["id"]) == st.session_state.session_id
        ),
        None,
    )


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_session_time(value: str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.strftime("%d/%m %H:%M") if parsed else ""


def session_group(value: str | None) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return "Anteriores"
    today = datetime.now(parsed.tzinfo).date()
    date = parsed.date()
    if date == today:
        return "Hoy"
    if date == today - timedelta(days=1):
        return "Ayer"
    if date >= today - timedelta(days=7):
        return "Últimos 7 días"
    if date >= today - timedelta(days=30):
        return "Últimos 30 días"
    return "Anteriores"


def short_title(title: str, max_length: int = 34) -> str:
    return title if len(title) <= max_length else title[: max_length - 1].rstrip() + "…"


def set_feedback_action(run_id: str, decision: str, comment: str | None = None) -> None:
    st.session_state.feedback_action = {
        "run_id": run_id,
        "decision": decision,
        "comment": comment,
    }


def render_review(
    review: dict[str, Any],
    *,
    active: bool,
    trace: list[dict[str, Any]] | None = None,
    llm_usage: dict[str, Any] | None = None,
    validation_payload: dict[str, Any] | None = None,
    llm_approval_estimate: dict[str, Any] | None = None,
) -> None:
    interpretation = str(review.get("interpretation") or "")
    sources = [str(item) for item in review.get("source_objects") or []]
    assumptions = [str(item) for item in review.get("assumptions") or []]
    validation_payload = validation_payload or {}
    security = validation_payload.get("security_validation") or {}
    autonomous = validation_payload.get("autonomous_investigation") or review.get("autonomous_investigation") or {}
    if autonomous:
        current_task_id = autonomous.get("current_task_id")
        plan_tasks = ((autonomous.get("plan") or {}).get("tasks") or [])
        current_task = next(
            (item for item in plan_tasks if item.get("task_id") == current_task_id),
            None,
        )
        if current_task:
            st.info(
                f"Tarea {current_task_id} delegada a {current_task.get('specialist')}: "
                f"{current_task.get('title') or current_task.get('objective')}",
                icon="🧭",
            )

    st.markdown("**Interpretación**")
    st.markdown(interpretation or "No se registró una interpretación.")
    st.markdown("**SQL propuesto**")
    st.code(review.get("sql", ""), language="sql")
    render_compact_model_usage(llm_usage, llm_approval_estimate)
    render_query_explanation(
        interpretation=interpretation,
        sources=sources,
        assumptions=assumptions,
        max_rows=security.get("enforced_limit") or security.get("max_rows"),
    )
    render_advanced_details(
        validation_payload,
        usage=llm_usage,
        estimate=llm_approval_estimate,
        trace=trace,
        domain=review.get("domain"),
        revision=int(review.get("revision", 1)),
        sources=sources,
        assumptions=assumptions,
    )

    if not active:
        st.caption("Esta propuesta ya fue procesada y se conserva en el historial.")
        return

    run_id = str(review["run_id"])
    revision = int(review.get("revision", 1))
    form_key = f"feedback-form-{run_id}-{revision}"
    feedback_key = f"feedback-{run_id}-{revision}"
    with st.form(form_key, clear_on_submit=True):
        feedback = st.text_area(
            "Cambios solicitados",
            placeholder="Ejemplo: usa el último mes cerrado y excluye comercios de prueba",
            key=feedback_key,
        )
        approve, change, reject = st.columns(3)
        approved = approve.form_submit_button(
            "Aprobar y ejecutar",
            type="primary",
            width="stretch",
        )
        change_requested = change.form_submit_button(
            "Solicitar cambios",
            width="stretch",
        )
        rejected = reject.form_submit_button("Rechazar", width="stretch")

    if approved:
        set_feedback_action(run_id, "approve")
    elif change_requested:
        if feedback.strip():
            set_feedback_action(run_id, "request_changes", feedback.strip())
        else:
            st.warning("Describe el cambio que debe aplicar el agente.")
    elif rejected:
        set_feedback_action(run_id, "reject", feedback.strip() or None)


def _conversation_contains_error(
    messages: list[dict[str, Any]], error: str | None
) -> bool:
    """Avoid rendering the same terminal run error as transient and persisted UI state."""
    normalized = " ".join(str(error or "").split())
    if not normalized:
        return False
    for message in reversed(messages):
        metadata = message.get("metadata") or {}
        payload = metadata.get("payload") or {}
        persisted = " ".join(str(payload.get("error") or "").split())
        if persisted and persisted == normalized:
            return True
    return False


def render_message(client: ApiClient, message: dict[str, Any]) -> None:
    metadata = message.get("metadata") or {}
    message_type = metadata.get("message_type")
    role = message.get("role", "assistant")
    with st.chat_message(role):
        if message_type == "sql_review":
            review = metadata.get("review") or {}
            payload = metadata.get("payload") or {}
            pending = st.session_state.pending_run or {}
            pending_review = pending.get("review") or {}
            active = (
                pending.get("status") == "awaiting_approval"
                and str(pending.get("run_id")) == str(metadata.get("run_id"))
                and int(pending_review.get("revision", 0)) == int(review.get("revision", 0))
            )
            render_review(
                review,
                active=active,
                trace=payload.get("trace"),
                llm_usage=payload.get("llm_usage"),
                validation_payload=payload,
                llm_approval_estimate=payload.get("llm_approval_estimate"),
            )
            return
        payload = metadata.get("payload")
        if payload:
            render_result(client, payload)
        else:
            st.markdown(message.get("content") or "")
            if metadata.get("sql"):
                with st.expander("SQL asociado"):
                    st.code(metadata["sql"], language="sql")


def run_stream(events: Iterable[dict[str, Any]], initial_label: str) -> dict[str, Any] | None:
    final_payload: dict[str, Any] | None = None
    answer = ""
    answer_box = st.empty()
    with st.status(initial_label, expanded=True) as status:
        for event in events:
            event_type = event.get("type")
            data = event.get("data") or {}
            if event_type in {"run_started", "run_resumed"}:
                status.update(label="El agente está trabajando…", state="running")
            elif event_type == "stage":
                label = data.get("label", data.get("node", "Etapa completada"))
                detail = data.get("detail")
                status.markdown(f"✅ **{label}**")
                if detail:
                    status.caption(detail)
                summary = data.get("summary") or {}
                if summary.get("domain"):
                    status.caption(f"Dominio: {summary['domain']}")
                if summary.get("autonomous_tasks") is not None:
                    status.caption(f"Tareas del plan: {summary['autonomous_tasks']}")
                if summary.get("current_task_id"):
                    status.caption(f"Tarea delegada: {summary['current_task_id']}")
                if summary.get("supervisor_action"):
                    status.caption(f"Decisión del supervisor: {summary['supervisor_action']}")
                if summary.get("evidence_count") is not None:
                    status.caption(f"Evidencias acumuladas: {summary['evidence_count']}")
                if summary.get("critic_ready") is not None:
                    status.caption(
                        "Crítico: " + ("evidencia suficiente" if summary["critic_ready"] else "requiere revisión")
                    )
                if summary.get("example_count") is not None:
                    status.caption(f"Ejemplos seleccionados: {summary['example_count']}")
                if summary.get("row_count") is not None:
                    status.caption(f"Filas obtenidas: {summary['row_count']}")
                if summary.get("security_approved") is not None:
                    security_label = "aprobada" if summary["security_approved"] else "rechazada"
                    status.caption(f"Seguridad: {security_label}")
                    if summary.get("statement_type"):
                        status.caption(f"Sentencia: {summary['statement_type']}")
                    if summary.get("tables"):
                        status.caption("Fuentes: " + ", ".join(summary["tables"]))
                    if summary.get("enforced_limit") is not None:
                        status.caption(f"Límite aplicado: {summary['enforced_limit']} filas")
                    for violation in summary.get("violations") or []:
                        status.warning(violation)
                if summary.get("cost_approved") is not None:
                    cost_label = "dentro de límites" if summary["cost_approved"] else "rechazado"
                    status.caption(f"Costo: {cost_label}")
                    if summary.get("plan_cost") is not None:
                        status.caption(
                            f"Costo planner: {summary['plan_cost']} / "
                            f"{summary.get('max_plan_cost', '—')}"
                        )
                    if summary.get("plan_rows") is not None:
                        status.caption(f"Filas estimadas de salida: {summary['plan_rows']}")
                    if summary.get("max_node_rows") is not None:
                        status.caption(
                            f"Máximo de filas por nodo: {summary['max_node_rows']} / "
                            f"{summary.get('max_plan_rows', '—')}"
                        )
                    if summary.get("relation_bytes") is not None:
                        status.caption(
                            "Tamaño evaluado: " + format_bytes(summary["relation_bytes"])
                            + " / " + format_bytes(summary.get("max_relation_bytes"))
                        )
                    for warning in summary.get("warnings") or []:
                        status.warning(warning)
                if summary.get("expected_llm_calls") is not None:
                    status.caption(
                        "Al aprobar: "
                        f"{format_number(summary.get('expected_llm_calls'), 0)} llamadas LLM, "
                        f"aprox. {format_number(summary.get('estimated_future_tokens'), 0)} "
                        "tokens adicionales"
                    )
            elif event_type == "llm_usage":
                status.caption(
                    "Consumo LLM acumulado: "
                    f"{format_number(data.get('actual_total_tokens'), 0)} tokens reales "
                    f"en {format_number(data.get('call_count'), 0)} llamadas"
                )
            elif event_type == "answer_delta":
                answer += str(data.get("delta") or "")
                answer_box.markdown(answer + " ▌")
            elif event_type == "review":
                final_payload = data
                status.update(
                    label="SQL listo para revisión humana",
                    state="complete",
                    expanded=False,
                )
            elif event_type == "error":
                status.update(label="La ejecución encontró un error", state="error")
                status.error(data.get("message", "Error desconocido"))
            elif event_type == "completed":
                final_payload = data

        if answer:
            answer_box.markdown(answer)
        if final_payload:
            state = "error" if final_payload.get("status") == "failed" else "complete"
            if final_payload.get("status") == "awaiting_approval":
                label = "Consulta preparada; revisa el SQL"
            elif state == "error":
                label = "No fue posible completar la solicitud"
            else:
                label = "Respuesta completada"
            status.update(label=label, state=state, expanded=False)
    return final_payload


def feedback_display(action: dict[str, Any]) -> str:
    decision = action["decision"]
    if decision == "approve":
        return "Aprobé la consulta SQL para su ejecución."
    if decision == "reject":
        suffix = f" Motivo: {action['comment']}" if action.get("comment") else ""
        return "Rechacé la consulta SQL." + suffix
    return f"Solicité cambios en la consulta SQL: {action.get('comment', '')}"


@st.dialog("Eliminar conversación")
def delete_conversation_dialog(client: ApiClient, session: dict[str, Any]) -> None:
    st.warning(f"Se eliminará **{session['title']}** y todo su historial.")
    st.caption("La acción también elimina ejecuciones, feedback y checkpoints asociados.")
    cancel, confirm = st.columns(2)
    if cancel.button("Cancelar", width="stretch"):
        st.rerun()
    if confirm.button("Eliminar", type="primary", width="stretch"):
        client.delete_session(str(session["id"]))
        st.session_state.session_id = None
        st.session_state.messages = []
        st.session_state.pending_run = None
        refresh_conversations(client)
        st.rerun()


if not st.session_state.token:
    st.title("Axiz SQL Agent")
    st.caption("Analítica conversacional gobernada")
    with st.form("login"):
        username = st.text_input("Usuario", value="admin")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary", width="stretch")
    if submitted:
        try:
            auth = ApiClient().login(username, password)
            st.session_state.token = auth["access_token"]
            st.rerun()
        except Exception as exc:
            st.error(f"No fue posible autenticar: {exc}")
    st.stop()

client = ApiClient(st.session_state.token)
if not st.session_state.sessions or not st.session_state.session_id:
    try:
        refresh_conversations(client)
    except Exception as exc:
        st.error(f"No fue posible cargar las conversaciones: {exc}")
        st.stop()

with st.sidebar:
    st.markdown("<div class='sidebar-brand'>Axiz SQL Agent</div>", unsafe_allow_html=True)
    if st.button("＋ Nuevo chat", type="primary", width="stretch"):
        created = client.create_session()
        refresh_conversations(client, str(created["id"]))
        st.rerun()

    search = st.text_input(
        "Buscar chats",
        placeholder="Buscar conversaciones",
        label_visibility="collapsed",
    )
    filtered = [
        item
        for item in st.session_state.sessions
        if search.lower() in item["title"].lower()
    ]

    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict(
        (name, [])
        for name in ("Hoy", "Ayer", "Últimos 7 días", "Últimos 30 días", "Anteriores")
    )
    for session in filtered:
        grouped[session_group(session.get("updated_at"))].append(session)

    for group_name, sessions in grouped.items():
        if not sessions:
            continue
        st.markdown(f"<div class='session-group'>{group_name}</div>", unsafe_allow_html=True)
        for session in sessions:
            session_id = str(session["id"])
            active = session_id == st.session_state.session_id
            pending = " ⏳" if session.get("pending_run_id") else ""
            title = short_title(session["title"])
            row, menu = st.columns([0.84, 0.16], gap="small", vertical_alignment="center")
            with row:
                if st.button(
                    f"{'● ' if active else ''}{title}{pending}",
                    key=f"session-{session_id}",
                    width="stretch",
                    type="primary" if active else "secondary",
                    help=session["title"],
                ):
                    if not active:
                        load_conversation(client, session_id)
                        st.rerun()
            with menu:
                with st.popover("⋯"):
                    st.caption("Opciones del chat")
                    with st.form(f"rename-form-{session_id}"):
                        new_title = st.text_input(
                            "Nombre",
                            value=session["title"],
                            key=f"rename-title-{session_id}",
                        )
                        rename = st.form_submit_button("Renombrar", width="stretch")
                    if rename and new_title.strip() and new_title.strip() != session["title"]:
                        client.rename_session(session_id, new_title.strip())
                        refresh_conversations(client, session_id)
                        st.rerun()
                    if st.button(
                        "Eliminar chat",
                        key=f"delete-{session_id}",
                        width="stretch",
                    ):
                        delete_conversation_dialog(client, session)
            st.markdown(
                f"<div class='session-caption'>{format_session_time(session.get('updated_at'))}"
                f" · {session.get('message_count', 0)} mensajes</div>",
                unsafe_allow_html=True,
            )

    st.divider()
    st.session_state.show_agent_trace = st.toggle(
        "Mostrar actividad del agente",
        value=st.session_state.show_agent_trace,
        help="Muestra decisiones, herramientas y validaciones sin exponer razonamiento privado.",
    )
    if st.button("Cerrar sesión", width="stretch"):
        for key in (
            "token",
            "sessions",
            "session_id",
            "messages",
            "pending_run",
            "feedback_action",
        ):
            st.session_state[key] = (
                []
                if key in {"sessions", "messages"}
                else None
            )
        st.rerun()

selected = current_session()
st.title(selected["title"] if selected else "Nueva conversación")
st.markdown(
    "<div class='current-session'>Reporteria agentica SQL con HITL</div>",
    unsafe_allow_html=True,
)

if st.session_state.transient_agent_error:
    transient_error = st.session_state.transient_agent_error
    if not _conversation_contains_error(st.session_state.messages, transient_error):
        st.error(transient_error)
    st.session_state.transient_agent_error = None

for message in st.session_state.messages:
    render_message(client, message)

feedback_action = st.session_state.feedback_action
if feedback_action:
    st.session_state.feedback_action = None
    with st.chat_message("user"):
        st.markdown(feedback_display(feedback_action))
    with st.chat_message("assistant"):
        try:
            payload = run_stream(
                client.stream_feedback(
                    feedback_action["run_id"],
                    feedback_action["decision"],
                    feedback_action.get("comment"),
                ),
                "Aplicando tu decisión…",
            )
            if payload and payload.get("status") == "failed":
                st.session_state.transient_agent_error = (
                    payload.get("error") or "No fue posible continuar la ejecución."
                )
        except Exception as exc:
            st.error(f"No fue posible continuar la ejecución: {exc}")
    refresh_conversations(client, st.session_state.session_id)
    st.rerun()

if st.session_state.pending_run:
    st.info("Hay una consulta SQL pendiente de aprobación. Revísala antes de enviar otra pregunta.")

question = st.chat_input(
    "Pregunta sobre adquirencia, pagos o comercios",
    disabled=bool(st.session_state.pending_run),
)
if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            payload = run_stream(
                client.stream_start_run(st.session_state.session_id, question),
                "Analizando tu pregunta…",
            )
            if payload and payload.get("status") == "failed":
                st.session_state.transient_agent_error = (
                    payload.get("error") or "No fue posible completar la solicitud."
                )
        except Exception as exc:
            st.error(f"Error al ejecutar el agente: {exc}")
    refresh_conversations(client, st.session_state.session_id)
    st.rerun()
