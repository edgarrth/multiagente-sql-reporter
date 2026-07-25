from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from api_client import ApiClient

st.set_page_config(page_title="Axiz SQL Agent", page_icon="📊", layout="wide")
st.title("Axiz SQL Agent PoC")
st.caption("Text-to-SQL gobernado con aprobación humana, seguridad y validación de costo")

for key, default in {
    "token": None,
    "session_id": None,
    "messages": [],
    "pending_run": None,
}.items():
    st.session_state.setdefault(key, default)


def render_result(payload: dict) -> None:
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
            st.plotly_chart(px.bar(frame, x=x, y=y, title=spec.get("title")), use_container_width=True)
        elif chart_type == "line" and x in frame.columns and y:
            st.plotly_chart(px.line(frame, x=x, y=y, title=spec.get("title")), use_container_width=True)
        st.dataframe(frame, use_container_width=True, hide_index=True)
    if payload.get("caveats"):
        with st.expander("Advertencias"):
            for caveat in payload["caveats"]:
                st.markdown(f"- {caveat}")
    if payload.get("sql"):
        with st.expander("SQL ejecutado"):
            st.code(payload["sql"], language="sql")


if not st.session_state.token:
    with st.form("login"):
        username = st.text_input("Usuario", value="admin")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar", type="primary")
    if submitted:
        try:
            auth = ApiClient().login(username, password)
            st.session_state.token = auth["access_token"]
            session = ApiClient(st.session_state.token).create_session()
            st.session_state.session_id = session["id"]
            st.rerun()
        except Exception as exc:
            st.error(f"No fue posible autenticar: {exc}")
    st.stop()

client = ApiClient(st.session_state.token)
with st.sidebar:
    st.success("Sesión autenticada")
    if st.button("Nueva conversación"):
        session = client.create_session()
        st.session_state.session_id = session["id"]
        st.session_state.messages = []
        st.session_state.pending_run = None
        st.rerun()
    if st.button("Cerrar sesión"):
        for key in ("token", "session_id", "messages", "pending_run"):
            st.session_state[key] = None if key != "messages" else []
        st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message.get("payload"):
            render_result(message["payload"])
        else:
            st.markdown(message["content"])

pending = st.session_state.pending_run
if pending:
    review = pending["review"]
    with st.chat_message("assistant"):
        st.subheader("Revisión humana requerida")
        st.markdown(f"**Interpretación:** {review['interpretation']}")
        if review.get("assumptions"):
            st.markdown("**Supuestos:**")
            for assumption in review["assumptions"]:
                st.markdown(f"- {assumption}")
        st.code(review["sql"], language="sql")
        feedback = st.text_area(
            "Comentario para corregir la consulta",
            key=f"feedback-{pending['run_id']}",
        )
        approve, change, reject = st.columns(3)
        if approve.button("Aprobar y ejecutar", type="primary", use_container_width=True):
            payload = client.feedback(pending["run_id"], "approve")
            st.session_state.pending_run = None
            st.session_state.messages.append({"role": "assistant", "payload": payload})
            st.rerun()
        if change.button("Solicitar cambios", use_container_width=True):
            if not feedback.strip():
                st.warning("Describe el cambio solicitado.")
            else:
                payload = client.feedback(pending["run_id"], "request_changes", feedback)
                if payload["status"] == "awaiting_approval":
                    st.session_state.pending_run = payload
                else:
                    st.session_state.pending_run = None
                    st.session_state.messages.append({"role": "assistant", "payload": payload})
                st.rerun()
        if reject.button("Rechazar", use_container_width=True):
            payload = client.feedback(pending["run_id"], "reject", feedback)
            st.session_state.pending_run = None
            st.session_state.messages.append({"role": "assistant", "payload": payload})
            st.rerun()

if not st.session_state.pending_run:
    question = st.chat_input("Pregunta sobre adquirencia, pagos o comercios")
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        try:
            payload = client.start_run(st.session_state.session_id, question)
            if payload["status"] == "awaiting_approval":
                st.session_state.pending_run = payload
            else:
                st.session_state.messages.append({"role": "assistant", "payload": payload})
        except Exception as exc:
            st.session_state.messages.append(
                {"role": "assistant", "content": f"Error al ejecutar el agente: {exc}"}
            )
        st.rerun()
