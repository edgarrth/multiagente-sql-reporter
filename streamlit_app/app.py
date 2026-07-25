from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import ApiClient

st.set_page_config(page_title="Axiz SQL Agent", page_icon="📊", layout="wide")

st.markdown(
    """
    <style>
      [data-testid="stSidebar"] { min-width: 310px; max-width: 310px; }
      .session-caption { color: #6b7280; font-size: .78rem; margin-top: -.45rem; }
      .agent-step { font-size: .92rem; }
      .review-card { border: 1px solid rgba(128,128,128,.28); border-radius: 12px;
                     padding: .8rem 1rem; margin: .25rem 0 .75rem 0; }
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
}.items():
    st.session_state.setdefault(key, default)


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
    st.session_state.session_id = selected
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


def format_session_time(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d/%m %H:%M")
    except ValueError:
        return ""


def set_feedback_action(run_id: str, decision: str, comment: str | None = None) -> None:
    st.session_state.feedback_action = {
        "run_id": run_id,
        "decision": decision,
        "comment": comment,
    }
    st.rerun()


def render_review(review: dict[str, Any], *, active: bool) -> None:
    st.markdown("**Consulta SQL propuesta**")
    if review.get("interpretation"):
        st.markdown(f"**Interpretación:** {review['interpretation']}")
    if review.get("domain"):
        st.caption(f"Dominio: {review['domain']}")
    if review.get("assumptions"):
        with st.expander("Supuestos utilizados"):
            for assumption in review["assumptions"]:
                st.markdown(f"- {assumption}")
    if review.get("source_objects"):
        with st.expander("Fuentes semánticas"):
            for source in review["source_objects"]:
                st.code(source)
    st.code(review.get("sql", ""), language="sql")
    if not active:
        st.caption("Esta propuesta ya fue procesada. Se conserva como parte del historial.")
        return

    run_id = str(review["run_id"])
    feedback = st.text_area(
        "Cambios solicitados",
        placeholder="Ejemplo: usa el último mes cerrado y excluye comercios de prueba",
        key=f"feedback-{run_id}",
    )
    approve, change, reject = st.columns(3)
    if approve.button(
        "Aprobar y ejecutar",
        type="primary",
        use_container_width=True,
        key=f"approve-{run_id}",
    ):
        set_feedback_action(run_id, "approve")
    if change.button(
        "Solicitar cambios",
        use_container_width=True,
        key=f"change-{run_id}",
    ):
        if feedback.strip():
            set_feedback_action(run_id, "request_changes", feedback.strip())
        else:
            st.warning("Describe el cambio que debe aplicar el agente.")
    if reject.button(
        "Rechazar",
        use_container_width=True,
        key=f"reject-{run_id}",
    ):
        set_feedback_action(run_id, "reject", feedback.strip() or None)


def render_message(message: dict[str, Any]) -> None:
    metadata = message.get("metadata") or {}
    message_type = metadata.get("message_type")
    role = message.get("role", "assistant")
    with st.chat_message(role):
        if message_type == "sql_review":
            review = metadata.get("review") or {}
            pending = st.session_state.pending_run or {}
            pending_review = pending.get("review") or {}
            active = (
                pending.get("status") == "awaiting_approval"
                and str(pending.get("run_id")) == str(metadata.get("run_id"))
                and int(pending_review.get("revision", 0)) == int(review.get("revision", 0))
            )
            render_review(review, active=active)
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


st.title("Axiz SQL Agent")
st.caption("Analítica conversacional gobernada, con SQL de solo lectura y aprobación humana")

if not st.session_state.token:
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
    st.markdown("### Conversaciones")
    if st.button("＋ Nueva conversación", type="primary", use_container_width=True):
        created = client.create_session()
        refresh_conversations(client, str(created["id"]))
        st.rerun()

    search = st.text_input("Buscar", placeholder="Filtrar conversaciones", label_visibility="collapsed")
    filtered = [
        item
        for item in st.session_state.sessions
        if search.lower() in item["title"].lower()
    ]
    for session in filtered:
        session_id = str(session["id"])
        active = session_id == st.session_state.session_id
        pending = " ⏳" if session.get("pending_run_id") else ""
        prefix = "▸ " if active else ""
        if st.button(
            f"{prefix}{session['title']}{pending}",
            key=f"session-{session_id}",
            use_container_width=True,
            disabled=active,
        ):
            load_conversation(client, session_id)
            st.rerun()
        st.markdown(
            f"<div class='session-caption'>{format_session_time(session.get('updated_at'))}"
            f" · {session.get('message_count', 0)} mensajes</div>",
            unsafe_allow_html=True,
        )

    selected = current_session()
    if selected:
        with st.expander("Renombrar conversación"):
            new_title = st.text_input(
                "Título",
                value=selected["title"],
                key=f"rename-title-{selected['id']}",
            )
            if st.button("Guardar título", use_container_width=True):
                if new_title.strip() and new_title.strip() != selected["title"]:
                    client.rename_session(str(selected["id"]), new_title.strip())
                    refresh_conversations(client, str(selected["id"]))
                    st.rerun()

    st.divider()
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
