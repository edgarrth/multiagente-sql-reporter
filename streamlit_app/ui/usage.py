from __future__ import annotations

import os
from html import escape
from typing import Any

import streamlit as st


def _int(value: Any) -> int:
    return int(value or 0)


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _estimated_cost_usd(session_usage: dict[str, Any]) -> float:
    explicit_cost = session_usage.get("estimated_cost_usd")
    if explicit_cost is not None:
        return _float(explicit_cost)
    input_rate = _float(os.getenv("AXIZ_LLM_INPUT_USD_PER_1K"))
    output_rate = _float(os.getenv("AXIZ_LLM_OUTPUT_USD_PER_1K"))
    return (
        _int(session_usage.get("input_tokens")) * input_rate
        + _int(session_usage.get("output_tokens")) * output_rate
    ) / 1000


def _usage_chips(session_usage: dict[str, Any]) -> str:
    return "\n".join(
        (
            f"<span class=\"axiz-chip\">Entrada {_int(session_usage.get('input_tokens')):,}</span>",
            f"<span class=\"axiz-chip\">Salida {_int(session_usage.get('output_tokens')):,}</span>",
            f"<span class=\"axiz-chip\">Total {_int(session_usage.get('total_tokens')):,}</span>",
            f"<span class=\"axiz-chip\">{_int(session_usage.get('llm_calls')):,} llamadas</span>",
            f"<span class=\"axiz-chip\">Costo estimado (usd) {_estimated_cost_usd(session_usage):,.4f}</span>",
        )
    )


def render_header_usage_banner(session_usage: dict[str, Any]) -> None:
    st.markdown(
        f"""
        <div class="axiz-header-usage" title="Consumo acumulado de toda la sesi&oacute;n">
          {_usage_chips(session_usage)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_session_topbar(
    *,
    title: str | None,
    default_title: str,
    session_usage: dict[str, Any],
) -> None:
    st.markdown(
        f"""
        <div class="axiz-topbar">
          <div>
            <h1>{escape(title or default_title)}</h1>
            <div class="status">
              <span class="axiz-dot"></span>Reportería SQL autónoma · HITL activo
            </div>
          </div>
          <div class="axiz-usage" title="Consumo acumulado de toda la sesión">
            {_usage_chips(session_usage)}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_session_usage_summary(session_usage: dict[str, Any]) -> None:
    with st.expander("Uso total de tokens de la sesión", expanded=False):
        usage_columns = st.columns(6)
        usage_items = (
            ("Runs", session_usage.get("runs")),
            ("Llamadas LLM", session_usage.get("llm_calls")),
            ("Entrada", session_usage.get("input_tokens")),
            ("Salida", session_usage.get("output_tokens")),
            ("Total", session_usage.get("total_tokens")),
            ("Entrada en caché", session_usage.get("cached_input_tokens")),
        )
        for column, (label, value) in zip(usage_columns, usage_items, strict=True):
            column.metric(label, f"{_int(value):,}")

        reasoning_tokens = _int(session_usage.get("reasoning_output_tokens"))
        if reasoning_tokens:
            st.caption(f"Tokens de razonamiento reportados por el proveedor: {reasoning_tokens:,}")

        by_agent = {
            str(agent): _int(tokens)
            for agent, tokens in dict(session_usage.get("by_agent") or {}).items()
            if _int(tokens) > 0
        }
        if by_agent:
            st.markdown("**Consumo por agente**")
            rows = [
                {"Agente": agent, "Tokens": f"{tokens:,}"}
                for agent, tokens in sorted(
                    by_agent.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            ]
            st.dataframe(rows, hide_index=True, width="stretch")
