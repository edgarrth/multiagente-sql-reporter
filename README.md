# Axiz SQL Agent PoC

Versión `0.3.0`: experiencia conversacional persistente y streaming de progreso.

## Correcciones de compatibilidad

- OpenAI Responses API envía `verbosity` como `text.verbosity`.
- El repositorio de ejecuciones usa parámetros PostgreSQL con tipos explícitos y flags booleanos separados.
- Preguntas como `¿Qué puedes hacer?` se responden sin generar SQL ni invocar al LLM.
- Un fallo al persistir el estado de error ya no oculta la excepción original del agente.
- Streamlit consume eventos SSE y muestra el avance de cada nodo de LangGraph.
- Las conversaciones, revisiones SQL, decisiones HITL y respuestas se reconstruyen desde PostgreSQL.
- Las sesiones pueden listarse, seleccionarse y renombrarse desde la interfaz.


PoC empresarial de un agente multiagente Text-to-SQL gobernado. Convierte preguntas en lenguaje
natural en consultas SQL de solo lectura, solicita aprobación humana antes de ejecutar, aplica
validaciones determinísticas de seguridad y costo, verifica los resultados y devuelve una
explicación con tabla o gráfico.

El proyecto usa el namespace Python `axiz.pe.sql_agent` y no contiene dependencias de nombres o
implementaciones externas específicas.

# Capacidades implementadas

1. Clasifica la intención: consulta analítica, pregunta de catálogo o solicitud fuera de alcance.
2. Detecta el dominio entre los dominios publicados en el catálogo semántico.
3. Explora el catálogo semántico y sus contratos YAML.
4. Selecciona ejemplos SQL relevantes por similitud léxica y dominio.
5. Genera SQL estructurado mediante OpenAI Responses API u Ollama nativo.
6. Interrumpe el workflow para revisión humana de interpretación y SQL.
7. Corrige la consulta a partir del feedback humano o de errores del validador.
8. Valida seguridad con SQLGlot y costo con `EXPLAIN (FORMAT JSON)`.
9. Ejecuta con un rol PostgreSQL físico de solo lectura.
10. Verifica consistencia, filas vacías, truncamiento y correspondencia con la pregunta.
11. Explica los resultados y selecciona una visualización determinística.
12. Mantiene sesiones, memoria conversacional, auditoría y checkpoints persistentes.
13. Expone una interfaz Streamlit y un adaptador opcional para Microsoft Teams.
14. Permite asignar proveedor, modelo, contexto y parámetros de generación distintos a cada agente mediante presets YAML.
15. Publica progreso en tiempo real mediante SSE y presenta la respuesta de forma progresiva.
16. Persiste el historial completo, incluyendo propuestas SQL, feedback y revisiones sucesivas.
17. Permite cambiar entre sesiones anteriores y renombrarlas desde Streamlit.

# Arquitectura

```mermaid
flowchart LR
    U[Usuario] --> ST[Streamlit]
    U --> TM[Microsoft Teams]
    ST -->|JWT local + SSE| API[FastAPI]
    TM -->|JWT del canal y Entra ID| TA[Teams Adapter]
    TA -->|Internal service key| API

    API --> LG[LangGraph Workflow]
    LG --> IA[Intent & Domain Agent]
    LG --> SA[Semantic Explorer Agent]
    LG --> SQ[SQL Generator Agent]
    LG --> VA[Result Verifier Agent]
    LG --> EA[Explanation Agent]

    IA --> MR[Agent Model Registry]
    SQ --> MR
    VA --> MR
    EA --> MR
    MR --> OA[OpenAI Responses API]
    MR --> OL[Ollama native API]

    SA --> CAT[YAML Semantic Catalog]
    LG --> HITL[Human SQL Review]
    LG --> SG[SQLGlot Guardrails]
    LG --> CE[PostgreSQL EXPLAIN]
    LG --> EX[Read-only SQL Executor]

    API --> PG[(PostgreSQL\nSessions / Audit / Checkpoints)]
    API --> RD[(Redis\nCache / Teams pending state)]
    EX --> SEM[(PostgreSQL semantic views)]
```

# Flujo del agente

```mermaid
flowchart TD
    A[1. Clasificar intención] --> B[2. Detectar dominio]
    B --> C[3. Explorar catálogo]
    C --> D[4. Seleccionar ejemplos]
    D --> E[5. Generar SQL]
    E --> F[6. HITL: revisar SQL]
    F -->|Solicitar cambios| G[7. Corregir con feedback]
    G --> F
    F -->|Aprobar| H[8. Validar seguridad]
    H -->|Inválido y quedan reintentos| G
    H --> I[8. Validar costo]
    I --> J[9. Ejecutar como agent_reader]
    J --> K[10. Verificar resultado]
    K --> L[11. Explicar y visualizar]
    F -->|Rechazar| Z[Fin sin ejecutar]
```

# Base de datos de prueba

La PoC **genera sus propios datos sintéticos** durante la inicialización de PostgreSQL. No descarga
ni utiliza datos reales, PII o datos de tarjetas.

Volumen generado:

| Objeto | Volumen aproximado |
|---|---:|
| Comercios | 250 |
| Transacciones | 250 000 |
| Periodo transaccional | 365 días |
| Contracargos | Aproximadamente 900, derivados de transacciones aprobadas |
| Ciudades | 10 |
| MCC | 12 |
| Canales | POS, ECOMMERCE, CONTACTLESS y QR |
| Marcas | DINERS, VISA, MASTERCARD y AMEX |

Los datos son determinísticos y reproducibles. Incluyen aprobaciones, rechazos, reversos,
liquidaciones, códigos de respuesta, cuotas, transacciones internacionales, comisiones y
contracargos.

## Capas de datos

| Capa | Contenido | Acceso del agente |
|---|---|---|
| `operational` | Comercios, transacciones y contracargos sintéticos | Denegado |
| `analytics` | Dimensiones y hechos preparados | Denegado |
| `semantic` | Vistas certificadas sin información sensible | Solo `SELECT` |
| `app` | Usuarios, sesiones, mensajes, runs, feedback y auditoría | Solo backend |

## Modelo analítico

```mermaid
flowchart LR
    OM[operational.merchants] --> DM[analytics.dim_merchant]
    OT[operational.payment_transactions] --> FP[analytics.fact_payment_transactions]
    OC[operational.chargebacks] --> FC[analytics.fact_chargebacks]
    DD[analytics.dim_date]

    DM --> V1[semantic.v_payment_transactions]
    FP --> V1
    DM --> V2[semantic.v_daily_payment_metrics]
    FP --> V2
    DM --> V3[semantic.v_merchant_performance]
    FP --> V3
    DM --> V4[semantic.v_monthly_payment_metrics]
    FP --> V4
    DM --> V5[semantic.v_decline_analysis]
    FP --> V5
    DM --> V6[semantic.v_chargeback_metrics]
    FC --> V6
```

Vistas disponibles:

- `semantic.v_payment_transactions`
- `semantic.v_daily_payment_metrics`
- `semantic.v_merchant_performance`
- `semantic.v_monthly_payment_metrics`
- `semantic.v_decline_analysis`
- `semantic.v_chargeback_metrics`

El rol `agent_reader` tiene:

- `default_transaction_read_only=on`.
- `statement_timeout=20s`.
- `USAGE` únicamente sobre `semantic`.
- `SELECT` únicamente sobre vistas semánticas.
- Sin permisos sobre `operational`, `analytics` ni `app`.
- Sin permisos para crear objetos en `semantic`.

# Catálogo semántico

El catálogo se encuentra en `semantic_catalog/` y contiene:

```text
semantic_catalog/
├── global/
│   ├── calendars.yaml
│   └── glossary.yaml
└── domains/acquiring/
    ├── domain.yaml
    ├── entities/
    ├── metrics/
    ├── joins/
    ├── quality/
    ├── examples/
    └── trusted_queries/
```

Incluye:

- Entidades, grain, dimensiones y medidas.
- Métricas certificadas.
- Fuentes permitidas.
- Relaciones y reglas de join.
- Glosario y sinónimos.
- Reglas de calidad y freshness.
- Consultas confiables.
- Ejemplos NL-to-SQL.
- Clasificación de datos y políticas de acceso.

Para agregar otro dominio PostgreSQL no se modifica el grafo:

```text
semantic_catalog/domains/<nuevo-dominio>/
├── domain.yaml
├── entities/
├── metrics/
├── joins/
├── quality/
├── examples/
└── trusted_queries/
```

Luego se ejecuta `POST /api/v1/catalog/reload` o se reinicia la API.

# Configuración de modelos por agente

Cada agente LLM puede usar un proveedor, modelo y parámetros diferentes. Todo se resuelve desde
`config/agents.yaml`, sin condicionales de modelo dentro de los agentes o de LangGraph.

La configuración usa dos niveles:

- `presets`: valores recomendados por familia/modelo.
- `agents`: selección del preset y overrides específicos del especialista.

Ejemplo simplificado:

```yaml
presets:
  openai_gpt_5_6_terra_sql:
    provider: openai
    model: gpt-5.6-terra
    model_context_limit_tokens: 1050000
    context_window_tokens: 1050000
    max_input_tokens: 64000
    max_output_tokens: 5000
    reasoning_effort: high
    temperature: null
    top_p: null
    verbosity: low
    timeout_seconds: 120
    max_retries: 2
    service_tier: auto
    truncation: disabled

  ollama_qwen3_coder_30b_sql:
    provider: ollama
    model: qwen3-coder:30b
    base_url: ${OLLAMA_BASE_URL:-http://host.docker.internal:11434}
    model_context_limit_tokens: 262144
    context_window_tokens: 65536
    max_input_tokens: 56000
    max_output_tokens: 6000
    reasoning_effort: medium
    temperature: 0.1
    top_p: 0.9
    seed: 42
    timeout_seconds: 300
    max_retries: 1
    truncation: disabled
    ollama:
      top_k: 20
      repeat_penalty: 1.05
      keep_alive: 20m
      think: medium

agents:
  sql_generator:
    preset: ${AXIZ_SQL_GENERATOR_MODEL_PRESET:-openai_gpt_5_6_terra_sql}
```

## Parámetros configurables

| Parámetro | Uso | OpenAI | Ollama |
|---|---|---:|---:|
| `provider`, `model` | Selección del proveedor y modelo por agente | Sí | Sí |
| `base_url`, `api_key_env` | Endpoint y nombre de variable que contiene la credencial | Sí | Sí; la instancia local no requiere clave |
| `temperature` | Aleatoriedad de muestreo | Sí, cuando el perfil/modelo lo permite | Sí |
| `top_p` | Muestreo nucleus | Sí | Sí |
| `reasoning_effort` | Presupuesto de razonamiento | GPT-5.6 | Se traduce a `think` |
| `reasoning_mode` | Modo estándar o `pro` | GPT-5.6 | No aplica |
| `verbosity` | Nivel de detalle de salida | Modelos que soportan `text.verbosity` | No aplica |
| `model_context_limit_tokens` | Límite real documentado del modelo | Metadato validado | Metadato validado |
| `context_window_tokens` | Ventana asignada al agente | Presupuesto máximo de aplicación | Se envía como `num_ctx` |
| `max_input_tokens` | Presupuesto máximo del prompt | Sí, aplicado antes de llamar | Sí, aplicado antes de llamar |
| `input_overflow_strategy` | Qué hacer cuando el prompt excede el presupuesto | `error` recomendado; truncado solo si se habilita explícitamente | Igual |
| `max_output_tokens` | Máximo de salida | `max_output_tokens` | `num_predict` |
| `seed` | Reproducibilidad aproximada | No se envía por Responses API | Sí |
| `stop_sequences` | Secuencias de parada | No soportado por Responses API; la configuración falla rápido | `stop` |
| `timeout_seconds` | Timeout de llamada | Sí | Sí |
| `max_retries` | Reintentos transitorios | Sí | Sí; no reintenta errores HTTP 4xx |
| `store` | Persistencia de la respuesta en el proveedor | `false` por defecto | No aplica |
| `service_tier` | Tier de procesamiento (`auto`, `default`, `flex`, `scale`, `priority`) | Sí | No aplica |
| `truncation` | Estrategia del proveedor ante exceso de contexto | `disabled` por defecto para no perder contexto silenciosamente | No se envía; se controla antes de la llamada |
| `top_k`, `min_p` | Muestreo específico | No aplica | Sí |
| `repeat_penalty` | Penalización de repetición | No aplica | Sí |
| `keep_alive` | Permanencia del modelo en memoria | No aplica | Sí |

Para OpenAI, la ventana de contexto real pertenece al modelo y no se puede aumentar desde la
aplicación. `context_window_tokens` y `max_input_tokens` actúan como límites internos que pueden ser
menores, pero nunca mayores que `model_context_limit_tokens`. Para Ollama,
`context_window_tokens` también se envía como `num_ctx`; aumentarlo incrementa el consumo de
RAM/VRAM.

Los perfiles GPT-5.6 incluidos usan `reasoning_effort` y dejan `temperature`/`top_p` en `null`.
El perfil GPT-4.1 determinístico usa `temperature: 0.0`. Los presets Qwen usan temperaturas
bajas y orientadas a clasificación/Text-to-SQL estructurado; el preset gpt-oss conserva su
`temperature: 1.0` nativa y controla el esfuerzo con `think`. Son valores iniciales recomendados
para esta PoC y deben evaluarse con el dataset de pruebas antes de promoverlos a producción.

## Valores iniciales incluidos

| Preset | Uso recomendado | Contexto asignado | Entrada / salida | Muestreo y razonamiento |
|---|---|---:|---:|---|
| `openai_gpt_5_6_sol_quality` | Consultas/evaluaciones difíciles, priorizando calidad | 1.05M | 96K / 8K | `reasoning_effort: xhigh`, sin temperatura |
| `openai_gpt_5_6_luna_routing` | Intención y dominio, priorizando latencia/costo | 1.05M | 24K / 1.2K | `reasoning_effort: low`, sin temperatura |
| `openai_gpt_5_6_terra_sql` | Generación y reparación SQL | 1.05M | 64K / 5K | `reasoning_effort: high`, sin temperatura |
| `openai_gpt_5_6_luna_explanation` | Explicación y síntesis | 1.05M | 32K / 2.4K | `reasoning_effort: low`, `verbosity: medium` |
| `openai_gpt_4_1_deterministic` | Verificación y catálogo sin razonamiento | 1,047,576 | 32K / 2.2K | `temperature: 0.0` |
| `ollama_qwen3_8b_structured` | Clasificación local ligera | 32K de 40K | 24K / 1.6K | `temperature: 0.2`, `top_p: 0.9`, `seed: 42` |
| `ollama_qwen3_coder_30b_sql` | SQL local de mayor capacidad | 64K de 256K | 56K / 6K | `temperature: 0.1`, `top_p: 0.9`, `think: medium` |
| `ollama_gpt_oss_20b_reasoning` | Verificación local con razonamiento | 64K de 128K | 56K / 5K | `temperature: 1.0` nativa, `think: medium` |

La asignación de 64K para los modelos locales orientados a agente/código es un punto de partida; debe
reducirse cuando el hardware no tenga RAM/VRAM suficiente. El límite de entrada se mantiene por
debajo de la ventana para reservar espacio a la salida y al razonamiento.

## Cambiar proveedor sin modificar código

Usar OpenAI para generar SQL y Ollama para los demás agentes:

```dotenv
AXIZ_INTENT_DOMAIN_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_SQL_GENERATOR_MODEL_PRESET=openai_gpt_5_6_terra_sql
AXIZ_RESULT_VERIFIER_MODEL_PRESET=ollama_gpt_oss_20b_reasoning
AXIZ_EXPLANATION_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CATALOG_ANSWER_MODEL_PRESET=ollama_qwen3_8b_structured
```

También puede configurarse todo localmente:

```dotenv
AXIZ_INTENT_DOMAIN_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_SQL_GENERATOR_MODEL_PRESET=ollama_qwen3_coder_30b_sql
AXIZ_RESULT_VERIFIER_MODEL_PRESET=ollama_gpt_oss_20b_reasoning
AXIZ_EXPLANATION_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CATALOG_ANSWER_MODEL_PRESET=ollama_qwen3_8b_structured
```

Después de editar el YAML o cambiar variables, se reinicia la API o se llama:

```text
POST /api/v1/catalog/agent-models/reload
```

Los perfiles y presets efectivos se consultan mediante:

```text
GET /api/v1/catalog/agent-models
```

Ambos endpoints requieren rol `admin`.

# Multiagente y herramientas

| Componente | Tipo | Responsabilidad |
|---|---|---|
| Intent & Domain Agent | LLM | Clasifica intención y dominio |
| Semantic Explorer Agent | Determinístico con herramientas | Busca contratos y ejemplos |
| SQL Generator Agent | LLM | Genera y corrige SQL |
| Result Verifier Agent | LLM + reglas | Verifica que el resultado responda la pregunta |
| Explanation Agent | LLM + chart builder | Explica y prepara visualización |
| Catalog Answer Agent | LLM | Responde definiciones sin ejecutar SQL |
| SQLGlot Validator | Herramienta determinística | Analiza AST y bloquea SQL inseguro |
| PostgreSQL Cost Tool | Herramienta determinística | Evalúa `EXPLAIN`, filas, costo y tamaño |
| PostgreSQL Query Tool | Herramienta determinística | Ejecuta en transacción de solo lectura |

# Tecnologías

| Tecnología | Responsabilidad |
|---|---|
| Python 3.12 | Runtime común |
| FastAPI | API, autenticación, sesiones e integraciones |
| LangGraph | Máquina de estados, reintentos, HITL y checkpoints |
| OpenAI Responses API | Proveedor cloud con Structured Outputs y razonamiento configurable |
| Ollama native API | Proveedor local/cloud opcional con JSON Schema y parámetros `num_ctx`/`num_predict` |
| SQLGlot | Parsing AST, allowlist y bloqueo de SQL no permitido |
| Pydantic | Contratos tipados, configuración y Structured Outputs |
| PostgreSQL 17 | Dataset, sesiones, auditoría y checkpoints |
| Redis 7 | Estado temporal y continuidad de Teams |
| SQLAlchemy + psycopg 3 | Persistencia y ejecución SQL |
| Streamlit + Plotly | Chat persistente, SSE, HITL, tablas y gráficos |
| Microsoft 365 Agents SDK | Canal opcional de Microsoft Teams |
| Docker Compose | Entorno local reproducible |
| Pytest | Tests unitarios e integración |

# Autenticación

## Streamlit

La barra lateral lista conversaciones persistidas, permite cambiar de sesión, crear una nueva y renombrar la activa.

Para la PoC utiliza autenticación local:

1. Streamlit llama a `POST /api/v1/auth/login`.
2. FastAPI valida la contraseña con Argon2.
3. FastAPI emite un JWT.
4. Las sesiones y runs quedan asociados al usuario autenticado.

## Microsoft Teams

El adaptador de Teams es un servicio opcional y desacoplado. Valida el token del canal y utiliza la
identidad proveniente de Teams. Para producción se recomienda:

- Registrar la aplicación y bot en Microsoft Entra ID.
- Usar SSO/OAuth de Teams.
- Validar issuer, audience y tenant.
- Propagar el identificador del usuario al backend.
- Mantener una `INTERNAL_SERVICE_KEY` distinta entre Teams Adapter y FastAPI.

Una falla del adaptador de Teams no afecta FastAPI ni Streamlit porque se ejecuta en un perfil
Docker Compose independiente.


# Experiencia conversacional y persistencia

La interfaz no usa `st.session_state` como fuente de verdad del historial. PostgreSQL persiste:

- Sesiones y títulos.
- Preguntas del usuario.
- Propuestas SQL para HITL.
- Aprobaciones, rechazos y solicitudes de cambio.
- Cada nueva versión de una consulta corregida.
- Respuesta, tabla, especificación de gráfico, SQL y advertencias.

Streamlit consulta estos mensajes al abrir o cambiar de conversación. Una revisión corregida se agrega
como un turno nuevo; no reemplaza la propuesta anterior. La sesión se titula automáticamente con la
primera pregunta, y el título puede editarse manualmente.

Durante la ejecución, `POST /api/v1/agent/runs/stream` transmite eventos SSE por cada etapa del grafo:
clasificación, exploración semántica, generación SQL, seguridad, costo, ejecución, verificación y
explicación. La UI actualiza un panel de progreso y muestra la respuesta gradualmente. El flujo HITL
se reanuda por `POST /api/v1/agent/runs/{runId}/feedback/stream`.

# Estructura del proyecto

```text
axiz-pe-sql-agent-poc/
├── src/axiz/pe/sql_agent/    # Backend, agentes, herramientas y workflow
│   ├── agents/               # Especialistas LLM y explorador semántico
│   ├── api/routes/           # Endpoints FastAPI
│   ├── core/                 # Auth, PostgreSQL, Redis y logging
│   ├── models/               # Contratos Pydantic y estado LangGraph
│   ├── repositories/         # Usuarios, sesiones, runs y auditoría
│   ├── services/             # Model registry, OpenAI/Ollama, auth y Teams
│   ├── tools/                # Catálogo, SQLGlot, costo, ejecución y gráficos
│   └── workflow/             # Grafo, nodos y reanudación HITL
├── config/                   # Modelos configurables por agente
├── semantic_catalog/         # Dominios, métricas, joins, calidad y ejemplos
├── streamlit_app/            # Interfaz web
├── teams_adapter/            # Adaptador opcional Microsoft Teams
├── infrastructure/           # Dockerfiles, Compose e inicialización PostgreSQL
├── tests/                    # Tests unitarios e integración
├── docs/                     # Guías complementarias
├── pyproject.toml            # Dependencias y calidad
└── .env.example              # Variables documentadas
```

# Código principal

## `src/axiz/pe/sql_agent/workflow/graph.py`

Define el grafo genérico. No contiene tablas, métricas ni dominios codificados.

## `src/axiz/pe/sql_agent/workflow/nodes.py`

Implementa los once pasos. El nodo HITL usa `langgraph.types.interrupt` y reanuda mediante
`Command(resume=...)`.

## `src/axiz/pe/sql_agent/services/llm.py`

Carga presets por agente, valida presupuestos de contexto y enruta salidas estructuradas a
OpenAI `responses.parse` o a Ollama `/api/chat` con JSON Schema Pydantic.

## `src/axiz/pe/sql_agent/tools/sql_security.py`

Parsea el AST con SQLGlot y rechaza múltiples sentencias, DDL, DML, fuentes no autorizadas,
esquemas internos, joins cartesianos y funciones prohibidas.

## `src/axiz/pe/sql_agent/tools/sql_executor.py`

Ejecuta `EXPLAIN`, evalúa límites de costo y abre una transacción `READ ONLY` con el rol
`agent_reader`.

## `src/axiz/pe/sql_agent/tools/semantic_catalog.py`

Descubre dinámicamente los YAML de `semantic_catalog/domains/*`.

# Endpoints

| Orden | Método y ruta | Descripción funcional | Descripción técnica |
|---:|---|---|---|
| 1 | `GET /health/live` | Confirma que el proceso está activo | No consulta dependencias |
| 2 | `GET /health/ready` | Confirma que la PoC puede atender | Verifica PostgreSQL, Redis y catálogo |
| 3 | `POST /api/v1/auth/login` | Inicia sesión local | Valida Argon2 y emite JWT |
| 4 | `POST /api/v1/sessions` | Crea una conversación | Persiste sesión asociada al usuario |
| 5 | `GET /api/v1/sessions` | Lista conversaciones | Incluye cantidad de mensajes y run HITL pendiente |
| 6 | `PATCH /api/v1/sessions/{sessionId}` | Renombra una conversación | Actualiza el título validando propiedad |
| 7 | `GET /api/v1/sessions/{sessionId}/messages` | Recupera el historial | Devuelve mensajes y metadata de visualización/HITL |
| 8 | `GET /api/v1/catalog/domains` | Lista dominios | Lee el registro YAML dinámico |
| 9 | `GET /api/v1/catalog/agent-models` | Lista perfiles y presets | Solo admin; muestra proveedor, modelo, contexto y parámetros efectivos |
| 10 | `POST /api/v1/agent/runs` | Envía una pregunta sin streaming | Ejecuta LangGraph hasta HITL o fin |
| 11 | `POST /api/v1/agent/runs/stream` | Envía una pregunta interactiva | Emite progreso y respuesta mediante SSE |
| 12 | `POST /api/v1/agent/runs/{runId}/feedback` | Aprueba, rechaza o corrige sin streaming | Reanuda checkpoint LangGraph |
| 13 | `POST /api/v1/agent/runs/{runId}/feedback/stream` | Reanuda HITL interactivamente | Emite nuevas etapas y conserva revisiones anteriores |
| 14 | `GET /api/v1/agent/runs/{runId}` | Recupera estado | Lee estado y respuesta persistida |
| 15 | `POST /api/v1/catalog/reload` | Recarga catálogo | Solo admin; no requiere reinicio |
| 16 | `POST /api/v1/catalog/agent-models/reload` | Recarga modelos | Solo admin; afecta llamadas posteriores |
| 17 | `POST /api/v1/integrations/teams/messages` | Puente interno de Teams | Protegido por service key |

# Ejecución con Docker Compose

## 1. Preparar variables

```bash
cp .env.example .env
```

Cambiar como mínimo:

```dotenv
OPENAI_API_KEY=<api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
APP_SECRET_KEY=<mínimo-32-caracteres>
BOOTSTRAP_PASSWORD=<contraseña-segura>
INTERNAL_SERVICE_KEY=<service-key-segura>
```

## 2. Levantar PostgreSQL, Redis, API y Streamlit

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up --build -d
```

Accesos:

- Streamlit: `http://localhost:8501`
- FastAPI: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`
- PostgreSQL 18: `localhost:5432`
- Redis 8: `localhost:6379`

## 3. Usar Ollama instalado en el host

La PoC **no levanta Ollama dentro de Docker Compose**. El contenedor `api` se conecta a la
instalación de Ollama del host mediante:

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

El servicio `api` contiene la resolución adicional:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Esto permite usar los modelos ya descargados y la GPU administrada por Ollama en el host, sin crear
otro volumen ni duplicar modelos dentro de Docker.

Antes de iniciar la PoC, verificar Ollama en el host:

```bash
curl http://localhost:11434/api/tags
```

Después de levantar la infraestructura, validar también la conexión desde el contenedor:

```bash
make check-ollama
```

Descargar modelos en la instalación del host:

```bash
OLLAMA_MODELS="qwen3:8b" make pull-ollama
```

Para el preset SQL de 30B, descargarlo solo si el host tiene recursos suficientes:

```bash
OLLAMA_MODELS="qwen3-coder:30b" make pull-ollama
```

Después se seleccionan los presets `ollama_*` en `.env`. Ollama local no requiere API key.
`OLLAMA_API_KEY` se usa únicamente para endpoints Ollama que requieran autenticación.

Cuando FastAPI se ejecuta directamente fuera de Docker, usar:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
```

En Linux, si Ollama solo escucha en `127.0.0.1`, el contenedor podría no alcanzarlo. Se puede
configurar el servicio del host con `OLLAMA_HOST=0.0.0.0:11434`, reiniciar Ollama y proteger el
puerto 11434 con el firewall del host para no exponerlo a redes no confiables.

## 4. Levantar también Teams

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  --profile teams \
  up --build -d
```

La configuración detallada está en `docs/teams-setup.md`.

## 5. Ver logs

```bash
make logs
```

## 6. Reinicializar completamente la base

Los scripts de seed solo se ejecutan cuando el volumen PostgreSQL se crea por primera vez.
Después de cambiar el modelo o los scripts SQL:

```bash
make down
make up
```

`make down` elimina los volúmenes para regenerar los datos.

# Ejecución local sin Docker para API/UI

Requiere PostgreSQL y Redis disponibles:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[ui,dev]'
cp .env.example .env
```

Para ejecución local, ajustar:

```dotenv
AGENT_MODELS_CONFIG_PATH=config/agents.yaml
SEMANTIC_CATALOG_PATH=semantic_catalog
DATABASE_URL=postgresql+psycopg://app_owner:app_owner@localhost:5432/axiz_sql_agent
CHECKPOINT_DATABASE_URL=postgresql://app_owner:app_owner@localhost:5432/axiz_sql_agent
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_sql_agent
REDIS_URL=redis://localhost:6379/0
```

Ejecutar:

```bash
make run-api
make run-ui
```

# Pruebas

## Suite unitaria

```bash
pytest tests/unit -q
```

| Test | Qué valida |
|---|---|
| `test_agent_model_registry.py` | Presets OpenAI/Ollama, parámetros de muestreo, presupuesto de contexto y overrides de entorno |
| `test_semantic_catalog.py` | Descubrimiento de dominios, fuentes y selección de ejemplos |
| `test_sql_security.py` | SELECT permitido, límite automático y bloqueo de DML/esquemas internos |
| `test_chart_builder.py` | Selección determinística de gráfico según tipos de columnas |
| `test_auth.py` | Hash Argon2 y round-trip de JWT |
| `test_streaming_ui_contracts.py` | Contrato SSE, revisiones versionadas, feedback como nuevo turno y metadata de sesiones |

## Suite de integración de PostgreSQL

Primero levantar Docker y luego ejecutar:

```bash
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_sql_agent \
pytest tests/integration/test_read_only_database.py -q
```

| Test | Qué valida |
|---|---|
| `test_semantic_dataset_contains_realistic_volume` | Más de 200 000 filas visibles y 250 comercios |
| `test_semantic_views_cover_payments_declines_and_chargebacks` | Datos en vistas de pagos, rechazos y contracargos |
| `test_agent_role_cannot_modify_or_read_internal_layers` | Bloqueo físico de operational, analytics y CREATE |

## Suite completa

```bash
make test
```

# Ejemplos de preguntas

- ¿Cuál fue la tasa de aprobación de los últimos siete días por canal?
- ¿Qué comercios tuvieron mayor facturación el mes pasado?
- ¿Cómo evolucionó el monto procesado por MCC?
- ¿Cuáles fueron los principales códigos de rechazo de ayer?
- Compara la facturación del último mes cerrado con el anterior por marca.
- ¿Cuáles fueron los principales motivos de contracargo de los últimos seis meses?
- ¿Qué significa ticket promedio?

# Límites de la PoC

- El dataset es sintético; sirve para validar el flujo, no para benchmarking financiero.
- El catálogo incluido contiene un dominio. Agregar dominios no requiere modificar LangGraph, pero
  cada dominio necesita sus vistas, contratos y pruebas.
- PostgreSQL es el único `QueryTool` implementado. Otro motor requiere un adaptador nuevo, no cambios
  en los agentes ni en el grafo.
- Los modelos Ollama grandes requieren dimensionar RAM/VRAM y contexto; el perfil de 30B no es adecuado
  para todos los equipos de desarrollo.
- La integración Teams necesita registro real en Entra ID para una prueba end-to-end.
