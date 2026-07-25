# Validation report

Validaciones ejecutadas para la versión `0.4.2`:

- Compilación sintáctica Python: correcta para `src`, `streamlit_app`, `teams_adapter`, `tests` y `scripts`.
- Parsing TOML: correcto para `pyproject.toml`.
- Parsing YAML: correcto para catálogo semántico, modelos por agente y Docker Compose.
- Namespace Python: validado como `axiz.pe.sql_agent`.
- Docker Compose: PostgreSQL 18 con `axiz_agent_control` y `axiz_business_data` embebidas para la PoC, Redis 8, Ollama del host y externalización productiva parametrizable.
- Registro de modelos: perfiles OpenAI/Ollama y parámetros por agente validados.
- Adaptador OpenAI: `temperature`, `text.verbosity`, razonamiento, límites y Structured Outputs.
- Adaptador Ollama: `num_ctx`, `num_predict`, muestreo, semilla, repetición, `think`, `keep_alive` y JSON Schema.
- Streaming: contrato SSE validado para eventos de etapa, revisión, deltas de respuesta y finalización.
- Persistencia conversacional: contratos de sesiones, mensajes, run pendiente y revisión versionada validados.
- UI de conversaciones: sesión activa resaltada, grupos por fecha, menú `⋯`, renombrado y eliminación.
- HITL: formulario con `clear_on_submit` para limpiar el comentario después de enviar la decisión.
- Trazabilidad: `RunResponse.trace` persiste un resumen seguro de decisiones, herramientas y validaciones.
- Business data: `embedded` es el modo predeterminado de la PoC; `external` cambia únicamente la conexión productiva y ambos modos se reportan por readiness.
- Escaneo de referencias heredadas: no se encontraron identificadores anteriores.
- Pruebas ejecutadas: **31 aprobadas y 3 omitidas**.

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

# Business data embebida en PoC y externalizable en producción

- La PoC usa por defecto `BUSINESS_DATA_MODE=embedded`.
- Docker Compose crea y carga `axiz_business_data` con las capas `operational`, `analytics` y `semantic`.
- `AGENT_DATABASE_URL` apunta por defecto a `postgres:5432/axiz_business_data`.
- En producción, `BUSINESS_DATA_MODE=external` permite reemplazar únicamente el DSN del data plane.
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
8. Verificar `business_data_mode: embedded` en `GET /health/ready`.
9. Cambiar a `external` en un entorno de prueba y comprobar que no se modifica código.

## Bootstrap PostgreSQL 0.4.2

- El healthcheck de PostgreSQL usa la base administrativa `postgres`, que siempre existe.
- `postgres-bootstrap` crea de forma idempotente `axiz_agent_control` y, en modo embedded, `axiz_business_data`.
- La API depende de `service_completed_successfully` del bootstrap.
- El seed se omite cuando ya existen transacciones.
- La capa analytics/semantic se versiona mediante `public.axiz_bootstrap_metadata`.
- Los scripts de business data no hacen referencia al esquema `app` del control plane.

## Persistencia operativa

- `make down` conserva los volúmenes PostgreSQL y Redis.
- `make reset` elimina los volúmenes de forma explícita para regenerar la PoC.
