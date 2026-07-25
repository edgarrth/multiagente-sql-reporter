# Tests

- `unit/test_agent_model_registry.py`: verifica presets por agente, proveedores, muestreo, contexto, truncado explícito y overrides de entorno.
- `unit/test_llm_providers.py`: verifica la traducción exacta de parámetros hacia OpenAI Responses y Ollama `/api/chat`, incluyendo Structured Outputs/JSON Schema.
- `unit/test_semantic_catalog.py`: verifica descubrimiento dinámico y recuperación de ejemplos.
- `unit/test_chart_builder.py`: verifica selección determinística de visualización.
- `unit/test_auth.py`: verifica Argon2 y JWT.
- `unit/test_sql_security.py`: verifica políticas SQLGlot y límite de filas.
- `integration/test_read_only_database.py`: valida volumen, vistas semánticas, aislamiento y permisos físicos.
- `integration/test_control_database.py`: valida las tablas del control plane y que no exista la capa semántica.

Unitarios:

```bash
pytest tests/unit -q
```

Integración, después de iniciar Docker:

```bash
TEST_CONTROL_DSN=postgresql://app_owner:app_owner@localhost:5432/axiz_agent_control \
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_business_data \
pytest tests/integration -q
```

## Streaming y sesiones persistentes

`tests/unit/test_streaming_ui_contracts.py` valida el contrato SSE, la revisión versionada de SQL,
el feedback HITL como un turno nuevo y los campos necesarios para listar conversaciones persistidas.

La suite de integración también verifica que `agent_reader` se conecte a
`axiz_business_data`, que no pueda leer el esquema `app` y que la base
`axiz_agent_control` no contenga el esquema `semantic`.
