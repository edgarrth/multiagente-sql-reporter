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


def render_result(payload: dict[str, Any], *, include_answer: bool = True) -> None:
    if include_answer and payload.get("answer"):
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


def render_message(message: dict[str, Any]) -> None:
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
            render_review(review, active=active, trace=payload.get("trace"))
            return
        payload = metadata.get("payload")
        if payload:
            render_result(payload)
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
        ):
            st.session_state[key] = [] if key in {"sessions", "messages"} else None
        st.rerun()

selected = current_session()
st.title(selected["title"] if selected else "Nueva conversación")
st.markdown(
    "<div class='current-session'>Analítica gobernada · SQL de solo lectura · HITL</div>",
    unsafe_allow_html=True,
)

for message in st.session_state.messages:
    render_message(message)

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
