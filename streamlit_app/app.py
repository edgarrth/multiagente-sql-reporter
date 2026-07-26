from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
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
    "show_agent_trace": True,
    "excel_exports": {},
}.items():
    st.session_state.setdefault(key, default)


def render_trace(trace: list[dict[str, Any]] | None) -> None:
    if not trace or not st.session_state.show_agent_trace:
        return
    with st.expander("Actividad y decisiones del agente", expanded=False):
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


def render_validation_panel(payload: dict[str, Any]) -> None:
    security = payload.get("security_validation") or {}
    cost = payload.get("cost_validation") or {}
    has_security = bool(security)
    has_cost = bool(cost)
    if not has_security and not has_cost:
        return

    st.markdown("**Validación previa a la ejecución**")
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
            help="Máximo de filas permitido por la política SQL del agente.",
        )

    with cost_column:
        approved = bool(cost.get("approved"))
        if not has_cost:
            st.info("Costo no evaluado", icon="ℹ️")
        elif approved:
            st.success("Costo dentro de límites", icon="✅")
        else:
            st.error("Costo rechazado", icon="⛔")
        plan_cost = format_number(cost.get("total_cost"))
        max_plan_cost = format_number(cost.get("max_plan_cost"))
        st.metric(
            "Costo del planner",
            plan_cost,
            help=f"Límite configurado: {max_plan_cost}",
        )
        plan_rows = format_number(cost.get("plan_rows"), 0)
        max_plan_rows = format_number(cost.get("max_plan_rows"), 0)
        st.metric(
            "Filas estimadas",
            plan_rows,
            help=f"Límite configurado: {max_plan_rows}",
        )

    security_tab, cost_tab, plan_tab = st.tabs(
        ["Controles de seguridad", "Evaluación de costo", "Plan EXPLAIN"]
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
                "Métrica": "Filas estimadas",
                "Valor": format_number(cost.get("plan_rows"), 0),
                "Límite": format_number(cost.get("max_plan_rows"), 0),
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
        st.dataframe(pd.DataFrame(metrics), hide_index=True, use_container_width=True)
        semantic_tables = cost.get("tables") or []
        plan_relations = cost.get("plan_relations") or []
        if semantic_tables:
            st.markdown("**Fuentes semánticas recibidas:**")
            st.code("\n".join(str(item) for item in semantic_tables), language="text")
        if plan_relations:
            st.markdown("**Relaciones físicas detectadas en EXPLAIN:**")
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
        if explain_plan:
            st.json(explain_plan, expanded=False)
        else:
            st.caption("No hay un plan EXPLAIN disponible para esta respuesta.")


def render_llm_usage_panel(usage: dict[str, Any] | None) -> None:
    if not usage or not usage.get("call_count"):
        return

    st.markdown("**Consumo LLM**")
    calls, input_col, output_col, total_col = st.columns(4)
    calls.metric("Llamadas", format_number(usage.get("call_count"), 0))
    input_col.metric(
        "Tokens de entrada",
        format_number(usage.get("actual_input_tokens"), 0),
        help="Uso real reportado por OpenAI u Ollama. Incluye tokens cacheados cuando aplica.",
    )
    output_col.metric(
        "Tokens de salida",
        format_number(usage.get("actual_output_tokens"), 0),
        help="Uso real de salida. En modelos razonadores puede incluir tokens no visibles.",
    )
    total_col.metric(
        "Tokens totales",
        format_number(usage.get("actual_total_tokens"), 0),
        help=(
            "Total real reportado por el proveedor. La estimación máxima configurada fue "
            f"{format_number(usage.get('estimated_max_total_tokens'), 0)} tokens."
        ),
    )

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
            "Alguna llamada no devolvió métricas reales; los totales reales pueden ser parciales."
        )

    with st.expander("Detalle por agente y modelo"):
        rows: list[dict[str, Any]] = []
        for call in usage.get("calls") or []:
            rows.append(
                {
                    "Agente": call.get("agent"),
                    "Proveedor": call.get("provider"),
                    "Modelo": call.get("model"),
                    "Estado": call.get("status"),
                    "Entrada estimada": call.get("estimated_input_tokens"),
                    "Salida reservada": call.get("reserved_output_tokens"),
                    "Máximo estimado": call.get("estimated_max_total_tokens"),
                    "Entrada real": call.get("input_tokens"),
                    "Salida real": call.get("output_tokens"),
                    "Total real": call.get("total_tokens"),
                    "Cacheados": call.get("cached_input_tokens", 0),
                    "Razonamiento": call.get("reasoning_output_tokens", 0),
                    "Duración ms": round(float(call.get("duration_ms") or 0), 2),
                    "Intentos": call.get("attempt_count", 1),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(
            "Entrada estimada usa un presupuesto conservador previo a la llamada. "
            "Salida reservada es max_output_tokens, no consumo esperado. "
            "Los valores reales son los reportados por cada proveedor."
        )


def render_result(
    client: ApiClient,
    payload: dict[str, Any],
    *,
    include_answer: bool = True,
) -> None:
    if include_answer and payload.get("answer"):
        st.markdown(payload["answer"])
    if payload.get("key_findings"):
        st.markdown("**Hallazgos**")
        for finding in payload["key_findings"]:
            st.markdown(f"- {finding}")
    render_validation_panel(payload)
    render_llm_usage_panel(payload.get("llm_usage"))
    result = payload.get("result")
    if result and result.get("rows"):
        frame = pd.DataFrame(result["rows"])
        spec = payload.get("visualization") or {"type": "table"}
        chart_type = spec.get("type")
        x = spec.get("x")
        y = [column for column in spec.get("y", []) if column in frame.columns]
        if chart_type == "bar" and x in frame.columns and y:
            st.plotly_chart(
                px.bar(frame, x=x, y=y, title=spec.get("title")),
                use_container_width=True,
            )
        elif chart_type == "line" and x in frame.columns and y:
            st.plotly_chart(
                px.line(frame, x=x, y=y, title=spec.get("title")),
                use_container_width=True,
            )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.caption(
            f"{result.get('row_count', len(frame))} filas · "
            f"{result.get('elapsed_ms', 0):.0f} ms"
        )

        export = payload.get("export") or {}
        run_id = str(payload.get("run_id") or "")
        if export.get("available") and run_id:
            cached = st.session_state.excel_exports.get(run_id)
            if cached:
                st.download_button(
                    "⬇ Descargar Excel",
                    data=cached["content"],
                    file_name=cached["filename"],
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"download-excel-{run_id}",
                    on_click="ignore",
                    use_container_width=False,
                )
            elif st.button(
                "Preparar Excel",
                key=f"prepare-excel-{run_id}",
                help="Genera un XLSX gobernado con los resultados y metadatos de la consulta.",
            ):
                try:
                    with st.spinner("Generando archivo Excel…"):
                        content, filename = client.export_excel(run_id)
                    st.session_state.excel_exports[run_id] = {
                        "content": content,
                        "filename": filename,
                    }
                    st.rerun()
                except Exception as exc:
                    st.error(f"No fue posible generar el Excel: {exc}")
        elif export.get("reason"):
            st.caption(f"Exportación Excel no disponible: {export['reason']}")
    if payload.get("caveats"):
        with st.expander("Advertencias"):
            for caveat in payload["caveats"]:
                st.markdown(f"- {caveat}")
    if payload.get("sql"):
        with st.expander("SQL ejecutado"):
            st.code(payload["sql"], language="sql")
    render_trace(payload.get("trace"))
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
) -> None:
    st.markdown("**Consulta SQL propuesta**")
    if review.get("interpretation"):
        st.markdown(f"**Interpretación:** {review['interpretation']}")
    if review.get("domain"):
        st.caption(f"Dominio: {review['domain']} · Revisión {review.get('revision', 1)}")
    if review.get("assumptions"):
        with st.expander("Supuestos utilizados"):
            for assumption in review["assumptions"]:
                st.markdown(f"- {assumption}")
    if review.get("source_objects"):
        with st.expander("Fuentes semánticas"):
            for source in review["source_objects"]:
                st.code(source)
    st.code(review.get("sql", ""), language="sql")
    render_llm_usage_panel(llm_usage)
    render_trace(trace)
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
            use_container_width=True,
        )
        change_requested = change.form_submit_button(
            "Solicitar cambios",
            use_container_width=True,
        )
        rejected = reject.form_submit_button("Rechazar", use_container_width=True)

    if approved:
        set_feedback_action(run_id, "approve")
    elif change_requested:
        if feedback.strip():
            set_feedback_action(run_id, "request_changes", feedback.strip())
        else:
            st.warning("Describe el cambio que debe aplicar el agente.")
    elif rejected:
        set_feedback_action(run_id, "reject", feedback.strip() or None)


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
                        status.caption(
                            f"Filas estimadas: {summary['plan_rows']} / "
                            f"{summary.get('max_plan_rows', '—')}"
                        )
                    if summary.get("relation_bytes") is not None:
                        status.caption(
                            "Tamaño evaluado: " + format_bytes(summary["relation_bytes"])
                            + " / " + format_bytes(summary.get("max_relation_bytes"))
                        )
                    for warning in summary.get("warnings") or []:
                        status.warning(warning)
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
    if cancel.button("Cancelar", use_container_width=True):
        st.rerun()
    if confirm.button("Eliminar", type="primary", use_container_width=True):
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
        submitted = st.form_submit_button("Ingresar", type="primary", use_container_width=True)
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
    if st.button("＋ Nuevo chat", type="primary", use_container_width=True):
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
                    use_container_width=True,
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
                        rename = st.form_submit_button("Renombrar", use_container_width=True)
                    if rename and new_title.strip() and new_title.strip() != session["title"]:
                        client.rename_session(session_id, new_title.strip())
                        refresh_conversations(client, session_id)
                        st.rerun()
                    if st.button(
                        "Eliminar chat",
                        key=f"delete-{session_id}",
                        use_container_width=True,
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
    if st.button("Cerrar sesión", use_container_width=True):
        for key in (
            "token",
            "sessions",
            "session_id",
            "messages",
            "pending_run",
            "feedback_action",
            "excel_exports",
        ):
            st.session_state[key] = (
                []
                if key in {"sessions", "messages"}
                else ({} if key == "excel_exports" else None)
            )
        st.rerun()

selected = current_session()
st.title(selected["title"] if selected else "Nueva conversación")
st.markdown(
    "<div class='current-session'>Analítica gobernada · SQL de solo lectura · HITL</div>",
    unsafe_allow_html=True,
)

for message in st.session_state.messages:
    render_message(client, message)

feedback_action = st.session_state.feedback_action
if feedback_action:
    st.session_state.feedback_action = None
    with st.chat_message("user"):
        st.markdown(feedback_display(feedback_action))
    with st.chat_message("assistant"):
        try:
            run_stream(
                client.stream_feedback(
                    feedback_action["run_id"],
                    feedback_action["decision"],
                    feedback_action.get("comment"),
                ),
                "Aplicando tu decisión…",
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
            run_stream(
                client.stream_start_run(st.session_state.session_id, question),
                "Analizando tu pregunta…",
            )
        except Exception as exc:
            st.error(f"Error al ejecutar el agente: {exc}")
    refresh_conversations(client, st.session_state.session_id)
    st.rerun()
