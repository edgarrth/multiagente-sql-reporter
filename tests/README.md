# Tests

- `unit/test_agent_model_registry.py`: verifica presets por agente, proveedores, muestreo, contexto, truncado explícito y overrides de entorno.
- `unit/test_llm_providers.py`: verifica la traducción exacta de parámetros hacia OpenAI Responses y Ollama `/api/chat`, incluyendo Structured Outputs/JSON Schema.
- `unit/test_semantic_catalog.py`: verifica descubrimiento dinámico y recuperación de ejemplos.
- `unit/test_chart_builder.py`: verifica selección determinística de visualización.
- `unit/test_auth.py`: verifica Argon2 y JWT.
- `unit/test_sql_security.py`: verifica políticas SQLGlot y límite de filas.
- `integration/test_read_only_database.py`: valida volumen, vistas semánticas y permisos físicos.

Unitarios:

```bash
pytest tests/unit -q
```

Integración, después de iniciar Docker:

```bash
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_sql_agent \
pytest tests/integration/test_read_only_database.py -q
```
