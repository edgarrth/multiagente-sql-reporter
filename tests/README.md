# Tests

- `unit/test_agent_model_registry.py`: verifica presets por agente, proveedores, muestreo, contexto, truncado explícito y overrides de entorno.
- `unit/test_llm_providers.py`: verifica la traducción exacta de parámetros hacia OpenAI Responses y Ollama `/api/chat`, incluyendo Structured Outputs/JSON Schema.
- `unit/test_semantic_catalog.py`: verifica descubrimiento dinámico y recuperación de ejemplos.
- `unit/test_chart_builder.py`: verifica selección determinística de visualización.
- `unit/test_auth.py`: verifica Argon2 y JWT.
- `unit/test_sql_security.py`: verifica políticas SQLGlot y límite de filas.
- `unit/test_excel_export.py`: verifica elegibilidad, truncamiento, generación XLSX, metadata, nombres seguros y protección contra fórmulas.
- `unit/test_streamlit_api_client.py`: verifica la descarga diferida de Excel mediante `ApiClient.download_excel`.
- `integration/test_read_only_database.py`: valida volumen, vistas semánticas, aislamiento y permisos físicos.
- `integration/test_control_database.py`: valida las tablas del control plane y que no exista la capa semántica.

Unitarios:

```bash
pytest tests/unit -q
```

Integración, después de iniciar Docker:

```bash
TEST_CONTROL_DSN="postgresql://<owner-user>:<owner-password>@localhost:<postgres-port>/<control-database>" \
TEST_AGENT_DSN="postgresql://<reader-user>:<reader-password>@localhost:<postgres-port>/<business-database>" \
pytest tests/integration -q
```

## Streaming y sesiones persistentes

`tests/unit/test_streaming_ui_contracts.py` valida el contrato SSE, la revisión versionada de SQL,
el feedback HITL como un turno nuevo y los campos necesarios para listar conversaciones persistidas.

La suite de integración también verifica que `agent_reader` se conecte a
`axiz_business_data`, que no pueda leer el esquema `app` y que la base
`axiz_agent_control` no contenga el esquema `semantic`.

- `unit/test_query_engine_abstraction.py`: verifica el contrato `QueryEngine`, la fábrica y el alias de compatibilidad.
- `unit/test_model_catalog_validation.py`: verifica catálogo, probe estructurado y aliases privados.
- `unit/test_resilience_concurrency.py`: verifica columnas/índices de lease, idempotencia y wiring de coordinación.

## Feedback semántico general

`test_generalized_sql_feedback.py` valida el plan híbrido, transformaciones estructurales compuestas, reconciliación de filtros, cumplimiento semántico faltante y wiring LangGraph antes de seguridad/costo/HITL.
