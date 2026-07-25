# Validation report

Validaciones ejecutadas en el entorno de generación:

- Compilación sintáctica Python: correcta para `src`, `streamlit_app`, `teams_adapter`, `tests` y `scripts`.
- Parsing TOML: correcto para `pyproject.toml`.
- Parsing YAML: correcto para catálogo semántico, modelos por agente y Docker Compose.
- Namespace Python: validado como `axiz.pe.sql_agent`.
- Registro de modelos: validado con modelos y proveedores distintos por agente, presets, parámetros de generación y presupuestos de contexto.
- Adaptador OpenAI: validada la traducción de `temperature`, `verbosity`, `service_tier`, `truncation`, límites y Structured Outputs.
- Adaptador Ollama: validada la traducción de `num_ctx`, `num_predict`, muestreo, semilla, repetición, `think`, `keep_alive` y JSON Schema.
- Escaneo de referencias heredadas: no se encontraron identificadores anteriores.
- Pruebas unitarias ejecutadas: **15 aprobadas, 1 omitida**.

Prueba unitaria omitida:

1. Los tests de SQLGlot se omiten porque `sqlglot` no está instalado en el entorno de generación.
   La dependencia está declarada en `pyproject.toml` y se instala dentro de la imagen Docker.

Pruebas de integración no ejecutadas:

- El entorno no dispone de Docker, PostgreSQL ni `psycopg`. Los tests incluidos validan volumen de
  datos, vistas semánticas y permisos físicos del rol `agent_reader`.
- No se hicieron llamadas reales a OpenAI ni se descargaron modelos Ollama; los adaptadores se
  probaron con clientes simulados y validación exacta de sus payloads.

No fue posible ejecutar aquí el stack Docker end-to-end. Para completar la validación:

```bash
cp .env.example .env
make up
pytest tests/unit -q
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_sql_agent \
pytest tests/integration/test_read_only_database.py -q
```
