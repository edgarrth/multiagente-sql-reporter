# Validación técnica — Axiz SQL Agent PoC 0.11.3

# Alcance

Esta validación cubre la arquitectura de SQL autónomo abierto, los cuatro agentes, contratos
estructurados, auditoría de restricciones codificadas, acumulación de tokens por sesión e interfaz
Streamlit.

# Comandos

```bash
python -m compileall -q src streamlit_app tests scripts
python scripts/audit_agent_autonomy.py
python scripts/check_internal_imports.py
python scripts/check_agent_wiring.py
PYTHONPATH=/tmp/axiz_test_stubs:src pytest -q
```

# Controles verificados

- Solo cuatro clases de agentes de razonamiento.
- No existen módulos anteriores de feedback tipado o QuerySpec fijo.
- No hay regex de interpretación dentro de `agents/` o `skills/`.
- No existen filtros temporales, columnas predeterminadas ni formas de consulta universales en
  seguridad, configuración o catálogo.
- La proyección predeterminada conserva todos los contratos de fuente, métricas y dimensiones del
  dominio.
- El LLM recibe mensaje, catálogo y SQL anterior completo para revisiones.
- SQLGlot genera snapshots y diffs genéricos.
- Seguridad conserva allowlists, solo lectura, columnas publicadas y límites.
- La API agrega tokens de todos los runs de una sesión.
- Streamlit muestra consumo acumulado y consumo por consulta.
- La interfaz continúa siendo Streamlit; no contiene Angular ni Node.
- Todos los imports internos apuntan a módulos o símbolos empaquetados existentes.

# Limitaciones del entorno de empaquetado

El entorno local no dispone de todos los paquetes runtime. Para las pruebas que solo necesitaban
logging se utilizó un stub temporal externo de `structlog`. Las pruebas que necesitan SQLGlot,
LangGraph, Psycopg/PostgreSQL o Streamlit se omiten cuando la dependencia no está disponible.

No se ejecutaron llamadas reales a un proveedor LLM ni un E2E Docker con PostgreSQL y Redis.

# Resultado local

```text
152 pruebas aprobadas
9 pruebas omitidas
0 pruebas fallidas
```
