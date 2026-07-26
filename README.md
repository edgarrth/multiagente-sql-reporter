# Axiz SQL Agent PoC

Multiagente Text-to-SQL gobernado. Convierte preguntas en lenguaje
natural en consultas SQL de solo lectura, solicita aprobación humana antes de ejecutar, aplica
validaciones determinísticas de seguridad y costo, verifica los resultados y devuelve una
explicación con tabla o gráfico.

# Arquitectura

```mermaid
flowchart LR
    U[Usuario] --> ST[Streamlit]
    U --> TM[Microsoft Teams]
    ST -->|JWT local + SSE| API[FastAPI]
    TM -->|JWT del canal y Entra ID| TA[Teams Adapter]
    TA -->|Internal service key| API

    API --> LG[LangGraph Workflow]
    LG --> CR[Context Resolver Agent]
    LG --> IA[Intent & Domain Agent]
    LG --> CA[Conversation Context Agent]
    LG --> SA[Semantic Explorer Agent]
    LG --> FI[Feedback Interpreter Agent]
    LG --> SQ[SQL Generator Agent]
    LG --> FC[Feedback Compliance Agent]
    LG --> VA[Result Verifier Agent]
    LG --> EA[Explanation Agent]

    CR --> MR[Agent Model Registry]
    IA --> MR
    CA --> MR
    FI --> MR
    SQ --> MR
    FC --> MR
    VA --> MR
    EA --> MR
    MR --> OA[OpenAI Responses API]
    MR --> OL[Ollama native API]

    SA --> CAT[YAML Semantic Catalog]
    LG --> HITL[Human SQL Review]
    LG --> SG[SQLGlot Guardrails]
    LG --> CE[PostgreSQL EXPLAIN]
    LG --> EX[Read-only SQL Executor]

    subgraph PG[PostgreSQL 18 — instancia de la PoC]
        CTRL[(axiz_agent_control
Sesiones / memoria estructurada / auditoría / checkpoints)]
        DATA[(axiz_business_data
Operational / analytics / semantic)]
    end

    API --> CTRL
    LG --> CTRL
    EX --> DATA
    API --> XT[Excel Export Tool]
    XT --> XLSX[Archivo XLSX
Resultados + metadatos]
    API --> RD[(Redis 8
Cache / estado temporal)]
```

# Flujo del agente

```mermaid
flowchart TD
    Q[Pregunta actual] --> R[1. Resolver contexto con app.session_memory]
    R -->|Referencia ambigua sin memoria| CL[Aclaración]
    R --> A[2. Clasificar intención]
    A -->|Pregunta sobre la sesión| M[Responder desde memoria estructurada sin SQL]
    A -->|Consulta analítica o catálogo| B[3. Detectar dominio]
    B --> C[4. Explorar catálogo]
    C --> D[5. Seleccionar ejemplos]
    D --> E[6. Generar SQL y contexto analítico estructurado]
    E --> AP[7. Aplicar cambios AST seguros]
    AP --> CV[8. Validar cumplimiento del feedback]
    CV -->|Faltan cambios y quedan reintentos| E
    CV --> H[9. Validar seguridad]
    H -->|Inválido y quedan reintentos| G[10. Corregir con feedback técnico]
    G --> E
    H --> I[11. Analizar costo y plan EXPLAIN]
    I --> T[12. Estimar tokens posteriores a la aprobación]
    T --> F[13. HITL: revisar SQL, controles y estimación]
    F -->|Solicitar cambios| FP[14. Interpretar feedback como plan tipado]
    FP -->|Solo AST| AP
    FP -->|Cambio semántico o híbrido| E
    F -->|Aprobar| J[15. Ejecutar como agent_reader]
    J --> K[16. Verificar resultado]
    K --> L[17. Explicar y visualizar]
    L --> PM[18. Actualizar memoria estructurada versionada]
    PM -->|Resultado tabular elegible| X[Tool de exportación Excel]
    F -->|Rechazar| Z[Fin sin ejecutar]
```

# Persistencia y separación de bases de datos

Utiliza dos bases PostgreSQL diferentes:

| Base | Propósito | Usuario principal | Acceso del agente SQL |
|---|---|---|---|
| `axiz_agent_control` | Autenticación, sesiones, mensajes, ejecuciones, feedback, auditoría y checkpoints de LangGraph | `app_owner` | Denegado |
| `axiz_business_data` | Datos operacionales sintéticos, modelo analítico y vistas semánticas | `app_owner` para carga; `agent_reader` para consulta | Solo `SELECT` sobre `semantic` |

En Docker ambas bases viven dentro de la misma instancia PostgreSQL y comparten un volumen. Esto
reduce el costo de la PoC, pero mantiene aislamiento lógico, credenciales y ciclos de conexión
independientes.

```text
PostgreSQL 18 — PoC
├── axiz_agent_control
│   ├── app.*
│   └── tablas internas de checkpoints LangGraph
└── axiz_business_data
    ├── operational.*
    ├── analytics.*
    └── semantic.*
```

Las conexiones predeterminadas son:

```dotenv
DATABASE_URL=postgresql+psycopg://app_owner:app_owner@postgres:5432/axiz_agent_control
CHECKPOINT_DATABASE_URL=postgresql://app_owner:app_owner@postgres:5432/axiz_agent_control
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

`BUSINESS_DATA_MODE` documenta el modo de despliegue (external para bd fuera del compose) y aparece en `/health/ready`. El acceso real
siempre se resuelve mediante `AGENT_DATABASE_URL`, por lo que la externalización no introduce ramas
específicas dentro del workflow.

Redis no es fuente de verdad. Solo mantiene caché y estado temporal con TTL. Las conversaciones,
revisiones SQL y auditoría siempre se reconstruyen desde `axiz_agent_control`.

# Estructura del control plane

## Tablas administradas por la aplicación

| Tabla | Grain | Responsabilidad |
|---|---|---|
| `app.users` | Un usuario | Identidad local o externa, roles y estado de acceso |
| `app.chat_sessions` | Una conversación | Título, propietario y fechas de actividad |
| `app.chat_messages` | Un turno del chat | Mensajes de usuario, asistente o sistema y metadata de SQL/gráfico/HITL |
| `app.agent_runs` | Una ejecución del workflow | Pregunta, estado, snapshot del grafo, error y tiempos |
| `app.session_memory` | Una memoria por conversación | Documento JSONB estructurado, revisión, último run y fecha de actualización |
| `app.human_feedback` | Una decisión HITL | Aprobación, rechazo o instrucción de corrección |
| `app.audit_events` | Un evento auditable | Cambios de estado, ejecución SQL y decisiones relevantes |
| `app.channel_sessions` | Un vínculo canal-conversación | Relación entre Teams, usuario y sesión interna |

LangGraph crea en la misma base sus tablas internas para checkpoints, blobs y escrituras pendientes.
Estas tablas son infraestructura del workflow y no forman parte del modelo de negocio.

# Estructura de datos de negocio

La PoC genera datos sintéticos durante la inicialización de `axiz_business_data`.
Incluyen aprobaciones, rechazos, reversos,
liquidaciones, códigos de respuesta, cuotas, transacciones internacionales, comisiones y
contracargos.

## Capa `operational`

Representa el origen transaccional sintético. Conserva un modelo cercano al sistema fuente y puede
contener atributos que no deben exponerse directamente al agente.

| Tabla | Grain | Descripción |
|---|---|---|
| `operational.merchants` | Un comercio | Maestro de comercio, MCC, ciudad, segmento, riesgo y vigencia |
| `operational.payment_transactions` | Una transacción | Evento de pago con monto, canal, marca, estado, respuesta, liquidación y comisión |
| `operational.chargebacks` | Un contracargo | Disputa asociada a una transacción aprobada |

El rol `agent_reader` no tiene `USAGE` ni `SELECT` sobre esta capa.

## Capa `analytics`

Transforma los datos operacionales a estructuras consistentes para análisis. Se eliminan registros
de prueba, se estandariza la fecha de negocio y se separan dimensiones y hechos.

| Tabla | Grain | Descripción |
|---|---|---|
| `analytics.dim_date` | Un día | Calendario, mes, trimestre, semana y fin de semana |
| `analytics.dim_merchant` | Un comercio | Dimensión depurada del comercio |
| `analytics.fact_payment_transactions` | Una transacción analítica | Hecho de pagos sin transacciones de prueba |
| `analytics.fact_chargebacks` | Un contracargo analítico | Hecho enriquecido con el comercio asociado |

Esta capa está preparada para joins y agregaciones, pero permanece oculta al LLM y al rol de
ejecución. Así se evita que el modelo improvise relaciones o fórmulas fuera del contrato publicado.

## Capa `semantic`

Es la **interfaz SQL gobernada** que consume el agente. Publica solamente campos autorizados,
granularidades conocidas y métricas coherentes. Las vistas no duplican los datos: consultan las
tablas `analytics` y encapsulan joins, exclusiones y fórmulas.

| Vista | Grain | Uso principal |
|---|---|---|
| `semantic.v_payment_transactions` | Una transacción | Exploración detallada autorizada de pagos |
| `semantic.v_daily_payment_metrics` | Día + MCC + ciudad + canal + marca | KPIs diarios, aprobación, ticket y liquidación |
| `semantic.v_merchant_performance` | Día + comercio | Desempeño y comparación de comercios |
| `semantic.v_monthly_payment_metrics` | Mes + MCC + canal + marca | Tendencias y comparaciones mensuales |
| `semantic.v_decline_analysis` | Día + dimensiones + código de respuesta | Causas y montos de rechazo |
| `semantic.v_chargeback_metrics` | Mes + dimensiones + motivo + estado | Evolución y composición de contracargos |

El rol `agent_reader` posee:

- `CONNECT` solamente a `axiz_business_data`.
- `default_transaction_read_only=on`.
- `statement_timeout=20s`.
- `USAGE` solamente sobre el esquema `semantic`.
- `SELECT` solamente sobre vistas semánticas.
- Sin permisos sobre `operational`, `analytics` o `axiz_agent_control`.
- Sin permisos de `CREATE`, DDL o DML.

# Diferencia entre datos, capa semántica SQL y catálogo semántico

El proyecto utiliza tres conceptos distintos que no deben confundirse:

| Elemento | Dónde vive | Contiene | Para qué sirve |
|---|---|---|---|
| Datos operacionales y analíticos | PostgreSQL, esquemas `operational` y `analytics` | Filas físicas y estructuras de análisis | Persistir y transformar información |
| Capa semántica SQL | PostgreSQL, esquema `semantic` | Vistas gobernadas y métricas ejecutables | Ser el único contrato de consulta del agente |
| Catálogo semántico | Archivos YAML en `semantic_catalog/` | Nombres, definiciones, grain, joins, sinónimos, ejemplos, calidad y políticas | Dar contexto al LLM para seleccionar y usar correctamente las vistas |

La vista SQL responde **cómo se calcula y consulta** una métrica. El YAML explica **qué significa,
cuándo usarla, qué sinónimos reconoce y qué restricciones debe respetar**. Ambos deben versionarse y
probarse juntos.

Ejemplo conceptual:

```text
Pregunta: “¿Cuál fue la tasa de aprobación por canal?”

Catálogo YAML
└── identifica approval_rate, canal, periodo y fuente autorizada

Vista semantic.v_daily_payment_metrics
└── ejecuta la fórmula certificada sobre datos analytics

SQLGlot + permisos PostgreSQL
└── impiden acceder a operational o modificar información
```

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
- Fuentes permitidas, que deben apuntar al esquema `semantic`.
- Relaciones y reglas de join.
- Glosario y sinónimos.
- Reglas de calidad y freshness.
- Consultas confiables.
- Ejemplos NL-to-SQL.
- Clasificación de datos y políticas de acceso.

Para agregar otro dominio no se modifica el grafo. Se publican sus vistas en el data plane y se
agrega el contrato correspondiente:

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
  context_resolver:
    preset: ${AXIZ_CONTEXT_RESOLVER_MODEL_PRESET:-openai_gpt_5_6_luna_routing}
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

La asignación de 64K para los modelos locales orientados a agente/código es un punto de partida; puede variar dependiendo de los recursos
de computo. El límite de entrada se mantiene por
debajo de la ventana para reservar espacio a la salida y al razonamiento.

## Cambiar proveedor sin modificar código

Usar OpenAI para generar SQL y Ollama para los demás agentes:

```dotenv
AXIZ_CONTEXT_RESOLVER_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_INTENT_DOMAIN_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CONVERSATION_CONTEXT_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_SQL_GENERATOR_MODEL_PRESET=openai_gpt_5_6_terra_sql
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

# Validación activa del catálogo de modelos

`ModelCatalogValidator` valida los perfiles efectivos después de aplicar presets y variables de entorno. 
Agrupa agentes que usan el mismo proveedor, base URL y modelo para no repetir probes innecesarios.

Modos disponibles:

| Modo | Validación | Uso recomendado |
|---|---|---|
| `off` | No llama al proveedor | Desarrollo aislado o pruebas unitarias |
| `catalog` | Verifica que el modelo exista en el catálogo del proveedor | Smoke test sin generación |
| `probe` | Catálogo más una respuesta estructurada mínima | PoC demostrable y despliegues compartidos |

Configuración:

```dotenv
MODEL_VALIDATION_ON_STARTUP=true
MODEL_VALIDATION_MODE=probe
MODEL_VALIDATION_FAILURE_POLICY=warn
MODEL_VALIDATION_TIMEOUT_SECONDS=20
MODEL_VALIDATION_CACHE_TTL_SECONDS=300
```
Endpoints:

```text
GET  /api/v1/models/validation
POST /api/v1/models/validation/refresh
```

`GET /health/ready` incluye `model_catalog`, el modo, fecha y cantidades válidas, con warning o inválidas. Con `MODEL_VALIDATION_FAILURE_POLICY=fail`, una validación inválida impide iniciar la API; con `warn`, la API inicia para diagnóstico, pero readiness informa el problema.

# Resiliencia, idempotencia y concurrencia

Cada ejecución se coordina en PostgreSQL para que funcione con más de un worker o réplica de API. `app.agent_runs` incorpora:

```text
idempotency_key
version
lease_owner
lease_expires_at
heartbeat_at
cancel_requested_at
started_at
```

El flujo es:

```text
Request
  ↓
Idempotency-Key
  ↓
Advisory lock por usuario
  ↓
Validar límite de concurrencia
  ↓
Crear o recuperar run
  ↓
Lease + heartbeat
  ↓
LangGraph / HITL
  ↓
Persistencia atómica + liberación del lease
```
Configuración:

```dotenv
RUN_LEASE_SECONDS=360
RUN_LEASE_HEARTBEAT_SECONDS=30
MAX_CONCURRENT_RUNS_PER_USER=2
MAX_CONCURRENT_LLM_CALLS=8
```

El cliente Streamlit genera una clave de idempotencia por acción y también la envía en el header. 
Consumidores externos pueden fijarla explícitamente para repetir con seguridad una solicitud después de un timeout de red.

Cancelación:

```text
POST /api/v1/agent/runs/{runId}/cancel
```

Una cancelación de un run en ejecución se registra y el heartbeat cancela la tarea propietaria. Un run detenido en HITL pasa inmediatamente a `cancelled`.

# Validación de seguridad y costo

La aprobación humana confirma que la interpretación y el SQL propuesto son aceptables para el usuario, pero **no sustituye los controles técnicos**. El workflow ejecuta seguridad y `EXPLAIN` **antes del HITL**, de modo que la persona aprueba el SQL normalizado junto con sus controles y su impacto estimado. Aprobar solo habilita la ejecución read-only y las llamadas posteriores de verificación y explicación.

## Validación de seguridad con SQLGlot

`SqlSecurityValidator` analiza el AST de la consulta y comprueba:

- exactamente una sentencia SQL;
- presencia de un `SELECT` y ausencia de `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER` o comandos equivalentes;
- uso exclusivo de fuentes publicadas en la allowlist del dominio;
- bloqueo de esquemas definidos en `denied_schemas`, como `operational`, `analytics` o esquemas internos;
- rechazo de `CROSS JOIN` y joins sin condición cuando la política lo exige;
- bloqueo de funciones configuradas en `denied_functions`;
- presencia de un filtro temporal sobre alguna columna requerida;
- normalización del SQL y aplicación de un `LIMIT` máximo.

La salida `SecurityValidation` incluye el resultado, SQL normalizado, tipo de sentencia, fuentes, columnas, reglas aplicadas, límite efectivo y violaciones detectadas.

## Validación de costo con PostgreSQL

`PostgresQueryEngine.estimate_cost` ejecuta `EXPLAIN (FORMAT JSON)` con la conexión de solo lectura y evalúa:

- `Total Cost` calculado por el planner;
- filas estimadas (`Plan Rows`);
- tamaño total de las relaciones físicas detectadas dentro del plan mediante `pg_total_relation_size`;
- timeout configurado para el análisis;
- límites máximos definidos para costo, filas y tamaño.

La salida `CostValidation` contiene valores observados, límites, fuentes semánticas, relaciones físicas, ancho estimado de la fila, cantidad de nodos y el plan `EXPLAIN`. Distingue `plan_rows` —filas estimadas que devuelve el nodo raíz— de `max_node_rows` —máximo de filas que algún paso podría procesar—. La política usa `max_node_rows` para evitar que un `LIMIT 500` oculte un escaneo mucho mayor. Si un límite es superado, la consulta no llega al HITL ni se ejecuta.

Variables relacionadas:

```dotenv
MAX_RESULT_ROWS=500
MAX_PLAN_ROWS=250000
MAX_PLAN_COST=150000
MAX_RELATION_BYTES=536870912
SQL_TIMEOUT_SECONDS=20
MAX_SQL_REPAIR_ATTEMPTS=2
```

# Multiagente y herramientas

Los agentes LLM producen contratos Pydantic estructurados. Las tools ejecutan operaciones determinísticas y no 
consumen tokens del modelo. Los dominios y reglas de negocio provienen del catálogo semántico; 
no se codifican dentro de los agentes.

## Agentes

| Agente | Entrada | Salida | Descripción breve |
|---|---|---|---|
| **Context Resolver Agent** (`ContextResolverAgent`) | Pregunta actual, `ConversationMemory` y últimos turnos acotados | `ContextResolutionOutput`: pregunta original, pregunta autocontenida, indicador de follow-up, campos heredados, confianza y aclaración opcional | Detecta instrucciones elípticas como “ahora solo Lima” o “y por canal”. Reescribe la solicitud antes del routing sin generar SQL ni inventar filtros; si falta memoria suficiente, solicita aclaración. |
| **Intent & Domain Agent** (`IntentDomainAgent`) | `question`, dominios publicados y últimas entradas de `conversation_history` | `IntentDomainOutput`: intención, dominio, confianza, justificación resumida y pregunta de aclaración | Clasifica la solicitud como analítica, catálogo, capacidades, seguimiento de sesión o fuera de alcance. Las preguntas explícitas de capacidades y contexto pueden resolverse determinísticamente. |
| **Conversation Context Agent** (`ConversationContextAgent`) | Pregunta sobre la sesión, `ConversationMemory` como fuente primaria y últimos turnos como soporte | `ConversationAnswerOutput`: respuesta, turnos referenciados y advertencias | Responde preguntas como “¿qué datos te pedí?”, “¿qué SQL ejecutaste?” o “¿qué resultado dio?” sin consultar la base. Los casos explícitos se resuelven de forma determinística; solo referencias ambiguas usan el LLM configurado. |
| **Semantic Explorer Agent** (`SemanticExplorerAgent`) | `question`, `domain` | Diccionario de contexto: definición del dominio, `catalog_hits`, `allowed_sources`, `query_policy` y `selected_examples` | Especialista basado en tools que recupera contratos semánticos y ejemplos. No genera SQL ni llama directamente a la base. |
| **Feedback Interpreter Agent** (`FeedbackInterpreterAgent`) | Comentario HITL, SQL anterior, contrato analítico actual, catálogo semántico y límite gobernado | `SqlFeedbackPlan`: estrategia, lista de `SqlChangeRequest`, necesidad de regeneración/aclaración y confianza | Descompone feedback libre o compuesto en cambios semánticos tipados. No genera SQL y conserva explícitamente lo que el usuario no pidió modificar. |
| **SQL Generator Agent** (`SqlGeneratorAgent`) | Pregunta autocontenida, `semantic_context`, memoria, historial, SQL anterior, `SqlFeedbackPlan` y resultado de cumplimiento previo | `SqlGenerationOutput`: SQL, interpretación, supuestos, métricas, dimensiones, filtros, periodo y fuentes | Genera o regenera una única consulta read-only. Debe aplicar todos los cambios del plan y corregir los incumplimientos detectados por la revisión anterior. |
| **Feedback Compliance Agent** (`FeedbackComplianceAgent`) | Plan tipado, SQL anterior, SQL revisado, contrato generado, aplicación gobernada y catálogo | `FeedbackSemanticComplianceOutput`: cumplimiento, cambios aplicados/faltantes/no solicitados, confianza y aclaración opcional | Revisa el significado analítico de la nueva propuesta. No sustituye seguridad ni costo y no acepta cambios solo porque la interpretación textual diga que se aplicaron. |
| **Result Verifier Agent** (`ResultVerifierAgent`) | Pregunta, interpretación, SQL y `QueryResult` | `VerificationOutput`: válido, confianza, observaciones y advertencias | Verifica que las columnas y filas obtenidas puedan responder la pregunta. Combina controles determinísticos con una revisión LLM; no habilita permisos ni reemplaza SQLGlot. |
| **Explanation Agent** (`ExplanationAgent.explain`) | Pregunta, interpretación, `QueryResult` y `VerificationOutput` | `ExplanationOutput`: respuesta, hallazgos, advertencias y especificación de visualización | Redacta una explicación fiel a los datos verificados y delega la selección final del gráfico a una tool determinística. |
| **Catalog Answer Agent** (`ExplanationAgent.answer_catalog_question`, perfil LLM independiente) | Pregunta y contexto del catálogo semántico | `CatalogAnswerOutput`: respuesta y advertencias | Responde definiciones, métricas, owners, fuentes y joins sin generar ni ejecutar SQL. Aunque comparte clase con `ExplanationAgent`, usa un modelo configurable independiente. |

## Tools

| Tool | Entrada | Salida | Descripción breve |
|---|---|---|---|
| **Semantic Catalog Tool** (`SemanticCatalogTool`) | Ruta del catálogo; para búsqueda: `query`, `domain`, `limit` | Dominios, documentos encontrados, allowlist de fuentes y políticas | Descubre y carga dinámicamente YAML bajo `semantic_catalog/domains/*`. Es la fuente de verdad para significado y gobierno. |
| **Example Selector Tool** (`ExampleSelectorTool`) | `question`, `domain`, límite | Lista de ejemplos NL-to-SQL priorizados | Selecciona ejemplos del dominio para orientar la generación sin codificar casos de negocio en Python. |
| **Structured Conversation Memory Service** (`StructuredConversationMemoryService`) | Memoria anterior, estado LangGraph y `RunResponse` | `ConversationMemory` acotada y lista para persistir | Fusiona de forma determinística la solicitud, interpretación, dominio, métricas, dimensiones, filtros, periodo, SQL, resultado, modelos y tokens. Limita la muestra de filas y no sobrescribe memoria analítica con preguntas de capacidades o catálogo. |
| **SQL Memory Extractor** (`SqlMemoryExtractor`) | SQL validado | Filtros y ventana temporal derivados del AST | Usa SQLGlot para completar de forma determinística filtros y expresiones temporales que el modelo no haya declarado. No ejecuta la consulta. |
| **SQL Feedback Plan Validator** (`SqlFeedbackPlanValidator`) | `SqlFeedbackPlan` y símbolos publicados del catálogo | Plan normalizado o aclaración | Normaliza targets a columnas/métricas/fuentes canónicas, deriva la estrategia AST/regenerate/hybrid y bloquea elementos no publicados antes de generar SQL. |
| **SQL Feedback Applier** (`SqlFeedbackApplier`) | SQL existente o regenerado, `SqlFeedbackPlan`, dialecto y `MAX_RESULT_ROWS` | `SqlFeedbackApplication`: SQL transformado, cambios aplicados/diferidos/fallidos, límites efectivos y advertencias | Aplica sobre SQLGlot transformaciones seguras de `LIMIT`, filtros básicos y `ORDER BY`. Los cambios de métrica, dimensión, agrupación, periodo o fuente se difieren a regeneración semántica. |
| **SQL Feedback Compliance Validator** (`SqlFeedbackComplianceValidator`) | Plan, SQL anterior/final, contrato generado, aplicación AST y revisión semántica | `FeedbackComplianceResult`: cumplimiento determinístico/semántico, checks, cambios faltantes/no solicitados e instrucción de retry | Verifica postcondiciones por cambio, combina evidencia AST con la revisión semántica y bloquea la nueva propuesta hasta que cumpla el feedback completo. |
| **SQL Security Validator** (`SqlSecurityValidator`) | SQL, `allowed_sources` y política del dominio | `SecurityValidation` | Parsea el AST con SQLGlot, bloquea operaciones y fuentes no permitidas, exige filtros y aplica el límite de filas. |
| **Query Engine** (`QueryEngine`) | SQL normalizado/aprobado y fuentes detectadas | `CostValidation`, `QueryResult` y `QueryEngineHealth` | Contrato neutral usado por LangGraph para salud, plan y ejecución, sin importar el driver físico. |
| **PostgreSQL Query Engine** (`PostgresQueryEngine`) | DSN y políticas de costo/timeout | Implementación de `QueryEngine` | Ejecuta `EXPLAIN`, mide relaciones y consulta dentro de `BEGIN READ ONLY`; aplica reintentos solo a errores transitorios. |
| **Chart Builder Tool** (`ChartBuilderTool`) | `QueryResult` y título | `VisualizationSpec` | Selecciona de forma determinística tabla, barras o línea según las columnas disponibles. |
| **Excel Export Tool** (`ExcelExportTool`) | Para elegibilidad: resultado y estado; para generación: resultado, run, pregunta, SQL y dominio | `ExcelExportAvailability` o bytes XLSX | Decide si el resultado es exportable y genera un libro con hojas `Resultados` y `Metadatos`, sin reejecutar SQL y con protección contra spreadsheet injection. |
| **LLM Usage Collector** (`LLMUsageCollector`) | Perfil efectivo, presupuesto previo y métricas devueltas por OpenAI/Ollama para cada llamada | `LLMCallUsage` por llamada y `LLMUsageSummary` agregado | Acumula consumo real por run usando contexto asíncrono aislado, conserva el acumulado entre interrupciones HITL y no expone chain-of-thought. |
| **LLM Approval Token Estimator** (`LLMApprovalTokenEstimator`) | Pregunta, interpretación, SQL normalizado, `SecurityValidation`, `CostValidation` y perfiles de `result_verifier`/`explanation` | `LLMApprovalEstimate` con llamadas previstas, entrada, salida, total probable y máximo configurado | Proyecta de forma determinística el consumo adicional que ocurriría después de aprobar, sin invocar ningún modelo. |
| **Model Catalog Validator** (`ModelCatalogValidator`) | Perfiles efectivos, credenciales y modo de validación | `ModelValidationReport` | Consulta el catálogo real del proveedor y opcionalmente ejecuta un probe mínimo de Structured Outputs por modelo único. |
| **Run Execution Coordinator** (`RunExecutionCoordinator`) | Run ID, propietario del lease y configuración de heartbeat | Context manager de ejecución | Mantiene el lease distribuido, detecta cancelación y evita que dos workers ejecuten el mismo run. |

## Contratos compartidos principales

| Contrato | Contenido |
|---|---|
| `ConversationMemory` | Snapshot versionado de la última consulta analítica: solicitud original/resuelta, dominio, métricas, dimensiones, filtros, periodo, SQL, esquema/muestra de resultado, respuesta, modelos y tokens |
| `ContextResolutionOutput` | Pregunta original, pregunta autocontenida, follow-up, campos heredados, confianza y aclaración opcional |
| `QueryFilter` / `TimeWindowContext` | Filtros normalizados y periodo analítico persistible |
| `ConversationAnswerOutput` | Respuesta basada únicamente en historial persistido, turnos referenciados y caveats |
| `SqlFeedbackPlan` / `SqlChangeRequest` | Plan tipado de corrección, estrategia híbrida y cambios de límite, filtros, periodo, dimensiones, métricas, agrupación, orden o fuente |
| `SqlFeedbackApplication` | Cambios AST aplicados, diferidos y fallidos, SQL efectivo y advertencias de política |
| `FeedbackComplianceResult` | Cumplimiento determinístico y semántico, evidencia por cambio, faltantes, modificaciones no solicitadas y retry |
| `QueryResult` | Motor, dialecto, columnas, filas, `row_count`, `elapsed_ms` y `truncated` |
| `QueryEngineCapabilities` / `QueryEngineHealth` | Capacidades y salud del adaptador de data plane |
| `ModelValidationReport` | Resultado real de catálogo/probe por modelo efectivo |
| `SecurityValidation` | Aprobación, SQL normalizado, tipo, fuentes, columnas, reglas, límite y violaciones |
| `CostValidation` | Aprobación, costo, filas de salida, ancho, máximo de filas por nodo, cantidad de nodos, bytes, fuentes, límites, warnings y plan `EXPLAIN` |
| `LLMCallUsage` | Agente, proveedor, modelo, estimación, uso real, caché, razonamiento, duración, intentos y estado de una llamada |
| `LLMUsageSummary` | Consumo real acumulado del run y detalle de todas las llamadas ya ejecutadas, incluyendo revisiones HITL |
| `LLMApprovalEstimate` | Llamadas LLM previstas tras aprobar, tokens estimados, máximo configurado, filas/ancho proyectados y supuestos |
| `VerificationOutput` | Validez funcional, confianza, observaciones y caveats |
| `RunResponse` | Estado, pregunta resuelta, revisión de memoria, HITL, respuesta, tabla, gráfico, SQL, trazas, validaciones, consumo real, estimación posterior a la aprobación y exportación |

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
| PostgreSQL 18 | Control plane y data plane embebido de la PoC |
| Redis 8 | Estado temporal y continuidad de Teams |
| SQLAlchemy + psycopg 3 | Persistencia y ejecución SQL |
| Streamlit + Plotly | Chat persistente, SSE, HITL, tablas y gráficos |
| XlsxWriter | Generación segura de libros XLSX en memoria |

# Exportación Excel gobernada

La exportación es una **tool**. No requiere razonamiento ni una llamada al LLM: opera sobre un resultado ya aprobado por 
HITL, validado por SQLGlot/costo y ejecutado con el rol de solo lectura.

## Cuándo aparece la opción

La UI muestra **Exportar Excel** únicamente cuando:

- El run terminó con estado `completed`.
- El resultado contiene columnas y al menos una fila.
- El número de filas no supera `EXCEL_EXPORT_MAX_ROWS`.
- El resultado no fue truncado, salvo que `EXCEL_EXPORT_ALLOW_TRUNCATED=true`.

No se ofrece exportación para respuestas de catálogo, preguntas de capacidades, resultados vacíos, errores, consultas rechazadas o resultados incompletos. La decisión es determinística y se publica en `RunResponse.export`.

## Contenido del archivo

- Hoja `Resultados`: encabezados, tabla de Excel, filtros, congelamiento de encabezado y formatos básicos.
- Hoja `Metadatos`: run ID, fecha, dominio, pregunta, cantidad de filas, tiempo de ejecución y SQL aprobado.
- Protección contra spreadsheet injection: XlsxWriter deshabilita `strings_to_formulas` y `strings_to_urls`, por lo que los valores se escriben como texto literal.
- Auditoría: cada descarga genera un evento `excel_exported` en `app.audit_events`.

Configuración:

```dotenv
EXCEL_EXPORT_ENABLED=true
EXCEL_EXPORT_MAX_ROWS=5000
EXCEL_EXPORT_ALLOW_TRUNCATED=false
```

El archivo se construye a partir del resultado persistido del run; la exportación no vuelve a ejecutar SQL 
ni amplía el alcance aprobado. La UI usa generación diferida de Streamlit: el endpoint se invoca cuando el usuario pulsa **Exportar Excel**, por lo que preparar y descargar ocurre en un solo clic y no se precargan archivos al abrir conversaciones históricas. `ApiClient.download_excel(run_id)` devuelve directamente los bytes del XLSX; `export_excel(run_id)` se conserva por compatibilidad. Si el resultado fue truncado, el comportamiento recomendado es refinar la pregunta o los filtros antes de exportar.

# Autenticación

## Streamlit

La barra lateral lista conversaciones persistidas, permite cambiar de sesión, crear una nueva y renombrar la activa.

Para la PoC utiliza autenticación local:

1. Streamlit llama a `POST /api/v1/auth/login`.
2. FastAPI valida la contraseña con Argon2.
3. FastAPI emite un JWT.
4. Las sesiones y runs quedan asociados al usuario autenticado.

## Microsoft Teams (BORRADR)

El adaptador de Teams es un servicio opcional y desacoplado. Valida el token del canal y utiliza la
identidad proveniente de Teams. Para producción se recomienda:

- Registrar la aplicación y bot en Microsoft Entra ID.
- Usar SSO/OAuth de Teams.
- Validar issuer, audience y tenant.
- Propagar el identificador del usuario al backend.
- Mantener una `INTERNAL_SERVICE_KEY` distinta entre Teams Adapter y FastAPI.

Una falla del adaptador de Teams no afecta FastAPI ni Streamlit porque se ejecuta en un perfil
Docker Compose independiente.


# Estructura del proyecto

```text
axiz-pe-sql-agent-poc/
├── src/axiz/pe/sql_agent/    # Backend, agentes, herramientas y workflow
│   ├── agents/               # Especialistas LLM y explorador semántico
│   ├── api/routes/           # Endpoints FastAPI
│   ├── core/                 # Auth, PostgreSQL, Redis y logging
│   ├── models/               # Contratos Pydantic y estado LangGraph
│   ├── repositories/         # Usuarios, sesiones, memoria estructurada, runs y auditoría
│   ├── query_engines/        # Contrato neutral, fábrica y adaptadores de motores
│   ├── services/             # Memoria, model registry, OpenAI/Ollama, auth y Teams
│   ├── tools/                # Catálogo, SQLGlot, memoria SQL, plan, estimación, ejecución y exportación
│   └── workflow/             # Grafo, nodos y reanudación HITL
├── config/                   # Modelos configurables por agente
├── semantic_catalog/         # Dominios, métricas, joins, calidad y ejemplos
├── streamlit_app/            # Interfaz web
├── teams_adapter/            # Adaptador opcional Microsoft Teams
├── infrastructure/           # Dockerfiles, Compose y bootstrap idempotente PostgreSQL
├── tests/                    # Tests unitarios e integración
├── docs/                     # Guías complementarias
├── pyproject.toml            # Dependencias y calidad
└── .env.example              # Variables documentadas
```

# Código principal

## `src/axiz/pe/sql_agent/agents/context_resolver_agent.py`

Detecta follow-ups analíticos y produce una pregunta autocontenida usando `ConversationMemory`. No genera SQL y solicita aclaración cuando la referencia no puede resolverse de forma segura.

## `src/axiz/pe/sql_agent/repositories/conversation_memory_repository.py`

Lee y actualiza de forma versionada `app.session_memory`, validando siempre que la sesión pertenezca al usuario autenticado.

## `src/axiz/pe/sql_agent/services/conversation_memory.py`

Construye una memoria acotada a partir del estado y la respuesta del run. Las consultas no analíticas no reemplazan la memoria de negocio.

## `src/axiz/pe/sql_agent/workflow/graph.py`

Define el grafo genérico. No contiene tablas, métricas ni dominios codificados.

## `src/axiz/pe/sql_agent/workflow/nodes.py`

Implementa los nodos del flujo, incluyendo validación y estimación previa al HITL. El nodo HITL usa `langgraph.types.interrupt` y reanuda mediante `Command(resume=...)`.

## `src/axiz/pe/sql_agent/services/llm.py`

Carga presets por agente, valida presupuestos de contexto y enruta salidas estructuradas a
OpenAI `responses.parse` o a Ollama `/api/chat` con JSON Schema Pydantic.

## `src/axiz/pe/sql_agent/tools/sql_security.py`

Parsea el AST con SQLGlot y rechaza múltiples sentencias, DDL, DML, fuentes no autorizadas,
esquemas internos, joins cartesianos y funciones prohibidas.

## `src/axiz/pe/sql_agent/query_engines/base.py`

Define el contrato `QueryEngine`, sus capacidades y health check neutral.

## `src/axiz/pe/sql_agent/query_engines/postgres.py`

Implementa PostgreSQL con Psycopg, `EXPLAIN`, lectura transaccional, límites y reintentos transitorios. `tools/sql_executor.py` queda únicamente como alias de compatibilidad.

## `src/axiz/pe/sql_agent/services/model_validation.py`

Valida catálogo y Structured Outputs para OpenAI-compatible y Ollama, cachea el reporte y aplica la política `warn` o `fail`.

## `src/axiz/pe/sql_agent/services/run_execution.py`

Mantiene heartbeat/cancelación del lease durante la ejecución y coordina la propiedad del run entre workers.

## `src/axiz/pe/sql_agent/tools/llm_token_estimator.py`

Estima de forma determinística las llamadas `result_verifier` y `explanation` que ocurrirían al aprobar. Usa el plan PostgreSQL y los presupuestos del registry, pero no invoca modelos ni genera cargos.

## `src/axiz/pe/sql_agent/tools/semantic_catalog.py`

Descubre dinámicamente los YAML de `semantic_catalog/domains/*`.

## `src/axiz/pe/sql_agent/tools/excel_export.py`

Evalúa de forma determinística si un resultado puede exportarse y genera un XLSX en memoria. El libro contiene una hoja `Resultados` con tabla y filtros, y una hoja `Metadatos` con pregunta, dominio, SQL, run, tiempos y condición de truncamiento. Escribe todos los textos como strings literales y deshabilita la conversión automática a fórmulas o enlaces.

# Endpoints

| Orden | Método y ruta | Descripción funcional | Descripción técnica |
|---:|---|---|---|
| 1 | `GET /health/live` | Confirma que el proceso está activo | No consulta dependencias |
| 2 | `GET /health/ready` | Confirma que la PoC puede atender | Verifica control DB, motor de datos, Redis, catálogo semántico y modelos |
| 3 | `POST /api/v1/auth/login` | Inicia sesión local | Valida Argon2 y emite JWT |
| 4 | `POST /api/v1/sessions` | Crea una conversación | Persiste sesión asociada al usuario |
| 5 | `GET /api/v1/sessions` | Lista conversaciones | Incluye cantidad de mensajes y run HITL pendiente |
| 6 | `PATCH /api/v1/sessions/{sessionId}` | Renombra una conversación | Actualiza el título validando propiedad |
| 7 | `DELETE /api/v1/sessions/{sessionId}` | Elimina un chat | Borra mensajes, runs, feedback y checkpoints asociados |
| 8 | `GET /api/v1/sessions/{sessionId}/messages` | Recupera el historial | Devuelve mensajes y metadata de visualización/HITL |
| 9 | `GET /api/v1/catalog/domains` | Lista dominios | Lee el registro YAML dinámico |
| 10 | `GET /api/v1/catalog/agent-models` | Lista perfiles y presets | Solo admin; muestra proveedor, modelo, contexto y parámetros efectivos |
| 11 | `GET /api/v1/models/validation` | Consulta el estado de los modelos | Devuelve catálogo, probe, latencia, warnings y errores por modelo único |
| 12 | `POST /api/v1/models/validation/refresh` | Revalida modelos bajo demanda | Ignora la caché y vuelve a consultar/probar los proveedores |
| 13 | `POST /api/v1/agent/runs` | Envía una pregunta sin streaming | Ejecuta LangGraph hasta HITL o fin; soporta `Idempotency-Key` |
| 14 | `POST /api/v1/agent/runs/stream` | Envía una pregunta interactiva | Emite progreso y respuesta mediante SSE |
| 15 | `POST /api/v1/agent/runs/{runId}/feedback` | Aprueba, rechaza o corrige sin streaming | Reanuda checkpoint LangGraph |
| 16 | `POST /api/v1/agent/runs/{runId}/feedback/stream` | Reanuda HITL interactivamente | Emite nuevas etapas y conserva revisiones anteriores |
| 17 | `POST /api/v1/agent/runs/{runId}/cancel` | Cancela una ejecución o revisión pendiente | Marca cancelación y detiene al propietario mediante heartbeat |
| 18 | `GET /api/v1/agent/runs/{runId}` | Recupera estado | Lee estado y respuesta persistida |
| 19 | `GET /api/v1/agent/runs/{runId}/exports/excel` | Descarga el resultado en Excel | Valida propiedad, estado, filas y truncamiento; genera XLSX y audita la descarga |
| 20 | `POST /api/v1/catalog/reload` | Recarga catálogo | Solo admin; no requiere reinicio |
| 21 | `POST /api/v1/catalog/agent-models/reload` | Recarga modelos | Solo admin; afecta llamadas posteriores |
| 22 | `POST /api/v1/integrations/teams/messages` | Puente interno de Teams | Protegido por service key |

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

## Bootstrap idempotente de PostgreSQL

El Compose no depende de que el volumen sea nuevo. El arranque sigue este orden:

```text
postgres → postgres-bootstrap → api → streamlit
```

`postgres` valida únicamente la base administrativa `postgres`. Después, `postgres-bootstrap`:

1. Crea `axiz_agent_control` cuando no existe.
2. Crea `axiz_business_data` cuando `BUSINESS_DATA_MODE=embedded`.
3. Crea o actualiza el rol `agent_reader`.
4. Aplica las tablas del control plane de forma idempotente.
5. Carga el dataset sintético solo cuando no existen transacciones.
6. Construye las capas `analytics` y `semantic` cuando cambia la versión del esquema.
7. Finaliza con una validación de tablas y vistas requeridas.

Esto permite actualizar desde una versión que todavía usaba `axiz_sql_agent` sin recibir `database "axiz_agent_control" does not exist`. La base anterior no se elimina automáticamente; puede respaldarse o retirarse manualmente después de validar la migración.

Variables relevantes:

```dotenv
POSTGRES_PASSWORD=app_owner
AGENT_READER_PASSWORD=agent_readonly
REFRESH_BUSINESS_DATA_ON_START=false
```

Para forzar una reconstrucción de `analytics` y `semantic` manteniendo los datos operacionales:

```dotenv
REFRESH_BUSINESS_DATA_ON_START=true
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
- PostgreSQL 18: `localhost:5432` (`axiz_agent_control` y `axiz_business_data`)
- Redis 8: `localhost:6379`

## 3. Fuente de datos de negocio: PoC embebida y producción externalizable

### Modo predeterminado de la PoC

No se requiere ninguna base externa. Docker Compose crea `axiz_business_data`, ejecuta los scripts de
inicialización y genera los datos sintéticos. Este es el modo que debe utilizarse para probar el proyecto:

```dotenv
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

Con `docker compose up`, la PoC incluye entonces:

- base de control `axiz_agent_control`;
- base de negocio `axiz_business_data`;
- capas `operational`, `analytics` y `semantic`;
- dataset sintético;
- rol `agent_reader` con acceso de solo lectura a `semantic`.

### Opción para producción

Cuando la solución se despliegue en producción, se puede conservar el control plane y externalizar
únicamente el data plane. Basta con cambiar variables de entorno:

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:password@db.example.com:5432/business_data?sslmode=verify-full&sslrootcert=/app/certs/root-ca.pem
AGENT_DATABASE_CONNECT_TIMEOUT_SECONDS=10
```

También puede utilizarse una base instalada en el host para pruebas de integración:

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:password@host.docker.internal:5432/business_data
```

## 4. Usar Ollama instalado en el host

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

## 5. Levantar también Teams

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  --profile teams \
  up --build -d
```

La configuración detallada está en `docs/teams-setup.md`.

## 6. Ver logs

```bash
make logs
```

## 7. Reinicializar completamente la base

Un servicio one-shot llamado `postgres-bootstrap` crea y valida las dos bases de forma idempotente en cada despliegue. A diferencia de `/docker-entrypoint-initdb.d`, también funciona cuando el volumen PostgreSQL ya existía. El seed sintético solo se ejecuta si la tabla transaccional está vacía y la capa analytics/semantic solo se reconstruye cuando cambia `BOOTSTRAP_SCHEMA_VERSION` o cuando `REFRESH_BUSINESS_DATA_ON_START=true`.

El arranque normal conserva todos los volúmenes:

```bash
make down
make up
```

Para reconstruir únicamente `analytics` y `semantic` sin borrar sesiones ni datos operacionales:

```dotenv
REFRESH_BUSINESS_DATA_ON_START=true
```

Usa el siguiente comando solo cuando quieras eliminar completamente sesiones, auditoría, caché y datos sintéticos:

```bash
make reset
make up
```
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
DATABASE_URL=postgresql+psycopg://app_owner:app_owner@localhost:5432/axiz_agent_control
CHECKPOINT_DATABASE_URL=postgresql://app_owner:app_owner@localhost:5432/axiz_agent_control
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_business_data
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
| `test_streaming_ui_contracts.py` | Contrato SSE, revisiones versionadas, traza explicable, feedback como nuevo turno y borrado de sesiones |
| `test_ui_and_external_database_config.py` | Menú contextual de chats, limpieza del feedback y configuración de PostgreSQL externo |
| `test_query_engine_abstraction.py` | Contrato neutral, fábrica PostgreSQL y alias de compatibilidad |
| `test_model_catalog_validation.py` | Catálogo real, probe estructurado y aliases privados no listados |
| `test_resilience_concurrency.py` | Columnas/índices de lease, idempotencia, advisory lock y claim HITL atómico |

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