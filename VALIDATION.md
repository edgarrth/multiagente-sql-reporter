# Validation report

Validaciones ejecutadas para la versión `0.4.0`:

- Compilación sintáctica Python: correcta para `src`, `streamlit_app`, `teams_adapter`, `tests` y `scripts`.
- Parsing TOML: correcto para `pyproject.toml`.
- Parsing YAML: correcto para catálogo semántico, modelos por agente y Docker Compose.
- Namespace Python: validado como `axiz.pe.sql_agent`.
- Docker Compose: PostgreSQL 18 de control, Redis 8, Ollama del host y `AGENT_DATABASE_URL` externo configurable.
- Registro de modelos: perfiles OpenAI/Ollama y parámetros por agente validados.
- Adaptador OpenAI: `temperature`, `text.verbosity`, razonamiento, límites y Structured Outputs.
- Adaptador Ollama: `num_ctx`, `num_predict`, muestreo, semilla, repetición, `think`, `keep_alive` y JSON Schema.
- Streaming: contrato SSE validado para eventos de etapa, revisión, deltas de respuesta y finalización.
- Persistencia conversacional: contratos de sesiones, mensajes, run pendiente y revisión versionada validados.
- UI de conversaciones: sesión activa resaltada, grupos por fecha, menú `⋯`, renombrado y eliminación.
- HITL: formulario con `clear_on_submit` para limpiar el comentario después de enviar la decisión.
- Trazabilidad: `RunResponse.trace` persiste un resumen seguro de decisiones, herramientas y validaciones.
- Base externa: el healthcheck local solo depende de `axiz_agent_control`; la disponibilidad de business data se reporta por readiness.
- Escaneo de referencias heredadas: no se encontraron identificadores anteriores.
- Pruebas ejecutadas: **29 aprobadas y 3 omitidas**.

Pruebas omitidas por dependencias o servicios no disponibles en el entorno de generación:

1. `tests/unit/test_sql_security.py`: requiere `sqlglot` cuando no está instalado localmente.
2. `tests/integration/test_read_only_database.py`: requiere `psycopg` y PostgreSQL activo.
3. `tests/integration/test_control_database.py`: requiere `psycopg` y PostgreSQL activo.

Estas dependencias están declaradas en `pyproject.toml` y se instalan en la imagen de la API.

# Mejoras UX 0.4.0

- Botón **Nuevo chat** que crea y selecciona una conversación vacía.
- Chats agrupados en Hoy, Ayer, Últimos 7 días, Últimos 30 días y Anteriores.
- La sesión actual se distingue con estilo primario y se muestra como título de la vista principal.
- Cada chat tiene un menú `⋯` para renombrar o eliminar.
- La eliminación limpia mensajes, runs, feedback y checkpoints asociados.
- El campo **Cambios solicitados** se limpia al enviar el formulario HITL.
- Las revisiones SQL siguen apareciendo como respuestas nuevas y versionadas.
- La opción **Mostrar actividad del agente** controla la traza persistida.
- La traza no almacena ni expone chain-of-thought privado.

# Base de negocio externa

- `AGENT_DATABASE_URL` se lee desde `.env` y no está fijado al servicio PostgreSQL de Compose.
- Se soporta una base en `host.docker.internal`, una IP privada o un hostname remoto.
- Se soportan parámetros TLS de Psycopg dentro del DSN.
- `infrastructure/certs/` se monta read-only en `/app/certs`.
- `AGENT_DATABASE_CONNECT_TIMEOUT_SECONDS` limita el tiempo de conexión.
- FastAPI y Streamlit pueden iniciar aunque la base analítica externa esté caída.
- `GET /health/ready` diferencia `control_database` y `business_data_database`.

# Validación end-to-end pendiente en este entorno

No se ejecutó Docker aquí. Para validar el stack completo:

```bash
cp .env.example .env
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
pytest tests/unit -q
TEST_CONTROL_DSN=postgresql://app_owner:app_owner@localhost:5432/axiz_agent_control \
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_business_data \
pytest tests/integration -q
```

Pruebas manuales recomendadas en Streamlit:

1. Crear tres chats y confirmar cuál aparece activo.
2. Renombrar un chat desde el menú `⋯` y recargar el navegador.
3. Eliminar un chat y verificar que se selecciona otro o se crea uno nuevo.
4. Enviar una pregunta y observar las etapas SSE.
5. Solicitar cambios y confirmar que el text area queda vacío.
6. Confirmar que la nueva propuesta SQL aparece como un mensaje nuevo.
7. Activar y desactivar la trazabilidad persistida.
8. Configurar una base externa y verificar `GET /health/ready`.
