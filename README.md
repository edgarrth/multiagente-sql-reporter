# Axiz SQL Agent PoC


Versión `0.6.2`: aplicación determinística de cambios estructurales solicitados durante HITL, comenzando por `LIMIT`; el feedback numérico se aplica al AST después de regenerar el SQL y siempre respeta `MAX_RESULT_ROWS`.

Versión `0.6.1`: abstracción formal `QueryEngine`, validación activa del catálogo y Structured Outputs de cada modelo, idempotencia de requests, leases distribuidos con heartbeat, recuperación de runs abandonados, cancelación y límites de concurrencia.

Versión `0.5.0`: memoria conversacional estructurada y versionada por sesión, `ContextResolverAgent` para convertir follow-ups elípticos en preguntas autocontenidas, persistencia de métricas/dimensiones/filtros/periodo/SQL/resultado y resolución contextual sin depender únicamente del historial textual.

Versión `0.4.9`: línea compacta de modelo/tokens, resultado abierto y SQL ejecutado colapsado, y memoria contextual persistente para preguntas sobre solicitudes, SQL, resultados y consumo anteriores sin ejecutar nuevamente la base.

Versión `0.4.8`: respuestas compactas antes y después del HITL, detalles técnicos colapsados, exportación Excel en un solo clic, corrección de `ApiClient.download_excel` y migración completa al parámetro `width` de Streamlit.

Versión `0.4.7`: plan de ejecución PostgreSQL legible y aislado de los resultados de negocio; validación técnica antes del HITL y estimación de tokens que se consumirían después de aprobar el SQL.

Versión `0.4.6`: medición y visualización del consumo real de tokens por agente, modelo y proveedor; conserva la corrección de wiring del exportador Excel y el panel de seguridad/costo.

Versión `0.4.5`: corrección del wiring de `ExcelExportTool` durante el arranque; conserva el panel visible de validación de seguridad/costo y la documentación completa de agentes y tools.

Versión `0.4.4`: panel visible de validación de seguridad/costo y documentación completa de inputs y outputs de cada agente y tool; conserva el bootstrap idempotente, la UX persistente y la exportación Excel gobernada.

## Correcciones de compatibilidad

- OpenAI Responses API envía `verbosity` como `text.verbosity`.
- El repositorio de ejecuciones usa parámetros PostgreSQL con tipos explícitos y flags booleanos separados.
- Preguntas como `¿Qué puedes hacer?` se responden sin generar SQL ni invocar al LLM.
- Un fallo al persistir el estado de error ya no oculta la excepción original del agente.
- Streamlit consume eventos SSE y muestra el avance de cada nodo de LangGraph.
- Las conversaciones, revisiones SQL, decisiones HITL y respuestas se reconstruyen desde PostgreSQL.
- Las sesiones se presentan como chats agrupados por fecha, con sesión activa claramente resaltada y menú contextual para renombrar o eliminar.
- El campo de feedback HITL se limpia después de aprobar, rechazar o solicitar cambios.
- Los cambios numéricos de `LIMIT` solicitados durante HITL se aplican determinísticamente al AST y no dependen de que el LLM los recuerde.
- Cada respuesta persiste una traza explicable de decisiones, herramientas y validaciones, sin exponer razonamiento privado del modelo.
- La persistencia del agente y la data consultada viven en bases lógicas diferentes.
- El rol de ejecución SQL no puede conectarse a la base de sesiones, auditoría o checkpoints.
- Los resultados tabulares completados pueden exportarse a XLSX en un solo clic, con generación diferida, metadatos, límites y auditoría.
- Cada run separa el consumo LLM ya ejecutado de la estimación de llamadas posteriores a la aprobación.
- El plan de ejecución se representa como una tabla de nodos PostgreSQL y no mezcla filas de negocio.
- Las preguntas sobre la propia sesión se enrutan a memoria conversacional estructurada y no se confunden con preguntas de capacidades.
- Los follow-ups analíticos se resuelven contra una memoria JSONB versionada antes de clasificar, explorar el catálogo o generar SQL.


PoC empresarial de un agente multiagente Text-to-SQL gobernado. Convierte preguntas en lenguaje
natural en consultas SQL de solo lectura, solicita aprobación humana antes de ejecutar, aplica
validaciones determinísticas de seguridad y costo, verifica los resultados y devuelve una
explicación con tabla o gráfico.

El proyecto usa el namespace Python `axiz.pe.sql_agent` y no contiene dependencias de nombres o
implementaciones externas específicas.

# Capacidades implementadas

1. Resuelve referencias conversacionales y convierte follow-ups analíticos en preguntas autocontenidas usando memoria estructurada.
2. Clasifica la intención: consulta analítica, pregunta de catálogo, capacidades, seguimiento de la conversación o solicitud fuera de alcance.
3. Detecta el dominio entre los dominios publicados en el catálogo semántico.
4. Explora el catálogo semántico y sus contratos YAML.
5. Selecciona ejemplos SQL relevantes por similitud léxica y dominio.
6. Genera SQL estructurado mediante OpenAI Responses API u Ollama nativo.
7. Valida el SQL con SQLGlot y analiza su plan con `EXPLAIN (FORMAT JSON)` antes del HITL.
8. Estima las llamadas y tokens LLM que ocurrirían después de aprobar.
9. Interrumpe el workflow para revisión humana de interpretación, SQL, seguridad, costo y consumo previsto.
10. Corrige la consulta a partir del feedback humano o de errores del validador.
11. Ejecuta con un rol PostgreSQL físico de solo lectura.
12. Verifica consistencia, filas vacías, truncamiento y correspondencia con la pregunta.
13. Explica los resultados y selecciona una visualización determinística.
14. Mantiene sesiones, memoria conversacional estructurada y versionada, auditoría y checkpoints persistentes.
15. Expone una interfaz Streamlit y un adaptador opcional para Microsoft Teams.
16. Permite asignar proveedor, modelo, contexto y parámetros de generación distintos a cada agente mediante presets YAML.
17. Publica progreso en tiempo real mediante SSE y presenta la respuesta de forma progresiva.
18. Persiste el historial completo, incluyendo propuestas SQL, feedback y revisiones sucesivas.
19. Permite cambiar, renombrar y eliminar chats desde un menú contextual similar a ChatGPT.
20. Persiste una traza segura de intención, dominio, contexto semántico, seguridad, costo, ejecución y verificación.
21. Incluye `axiz_business_data` dentro de Docker Compose para la PoC y permite externalizarla en producción mediante configuración, sin modificar código.
22. Exporta a Excel en un solo clic únicamente resultados tabulares elegibles mediante una tool determinística, sin añadir otro agente LLM.
23. Mantiene compacta la respuesta principal: interpretación, SQL, modelos y tokens; el resultado y los controles técnicos quedan en desplegables.
24. Desacopla LangGraph del motor físico mediante `QueryEngine` y `QueryEngineFactory`; PostgreSQL es la primera implementación.
25. Valida activamente cada modelo efectivo contra el catálogo del proveedor y mediante un probe mínimo de Structured Outputs.
26. Protege runs y decisiones HITL con idempotencia, versionado optimista, leases, heartbeat, recuperación de ejecuciones abandonadas y cancelación.


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
    LG --> SQ[SQL Generator Agent]
    LG --> VA[Result Verifier Agent]
    LG --> EA[Explanation Agent]

    CR --> MR[Agent Model Registry]
    IA --> MR
    CA --> MR
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

La PoC levanta por defecto una instancia PostgreSQL con **dos bases lógicas independientes**:
`axiz_agent_control` y `axiz_business_data`. Los scripts de Docker generan la estructura y los datos
sintéticos del data plane, de modo que la demostración funciona sin dependencias externas. Para un
despliegue productivo, `BUSINESS_DATA_MODE=external` y `AGENT_DATABASE_URL` permiten apuntar el
mismo agente a una base gobernada fuera de Docker, sin modificar LangGraph ni el código de los agentes.

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
    E --> H[7. Validar seguridad]
    H -->|Inválido y quedan reintentos| G[8. Corregir con feedback técnico]
    G --> H
    H --> I[9. Analizar costo y plan EXPLAIN]
    I --> T[10. Estimar tokens posteriores a la aprobación]
    T --> F[11. HITL: revisar SQL, controles y estimación]
    F -->|Solicitar cambios| C2[12. Corregir con feedback humano]
    C2 --> H
    F -->|Aprobar| J[13. Ejecutar como agent_reader]
    J --> K[14. Verificar resultado]
    K --> L[15. Explicar y visualizar]
    L --> PM[16. Actualizar memoria estructurada versionada]
    PM -->|Resultado tabular elegible| X[Tool de exportación Excel]
    F -->|Rechazar| Z[Fin sin ejecutar]
```

La resolución contextual ocurre antes del routing. Una pregunta como `ahora solo Lima` se transforma en una solicitud analítica autocontenida usando exclusivamente la memoria de la sesión actual. Si no existe un contexto previo suficiente, el grafo solicita aclaración y no genera SQL.

> [!IMPORTANT]
> La versión 0.5.0 agrega el nodo `resolve_context` antes de `classify`. Los runs que ya estén en `awaiting_approval` pueden terminarse con su checkpoint actual; las nuevas ejecuciones usarán memoria estructurada. No se deben eliminar los volúmenes: el bootstrap crea `app.session_memory` de forma idempotente.

# Persistencia y separación de bases de datos

En la versión anterior, sesiones y datos de negocio estaban en una sola base y se separaban por
esquemas. Desde la versión `0.3.1`, la PoC utiliza dos bases PostgreSQL diferentes:

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

En producción se recomienda desplegarlas en servicios o instancias distintas:

```text
Control plane PostgreSQL                 Plataforma de datos
axiz_agent_control                       Databricks / Fabric / Snowflake / PostgreSQL
- identidad y sesiones                   - datos operacionales o lakehouse
- auditoría y feedback                   - modelos analíticos
- checkpoints                            - capa semántica gobernada
          │                                         ▲
          └──────── API / workflow ─────────────────┘
                         conexión read-only
```

Esta separación es recomendable porque:

- Reduce el radio de impacto: una consulta pesada no degrada las sesiones o checkpoints.
- Evita que el rol generado para SQL tenga acceso al historial conversacional o a datos de identidad.
- Permite respaldar, retener y escalar cada carga con políticas diferentes.
- Facilita reemplazar PostgreSQL analítico por otro motor usando `AGENT_DATABASE_URL`.
- Permite aplicar controles de red, secretos y observabilidad diferentes al control plane y al data plane.

Las conexiones predeterminadas de la PoC son:

```dotenv
DATABASE_URL=postgresql+psycopg://app_owner:app_owner@postgres:5432/axiz_agent_control
CHECKPOINT_DATABASE_URL=postgresql://app_owner:app_owner@postgres:5432/axiz_agent_control
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

`BUSINESS_DATA_MODE` documenta el modo de despliegue y aparece en `/health/ready`. El acceso real
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

## Ciclo de persistencia conversacional

```mermaid
flowchart LR
    Q[Pregunta] --> M1[app.chat_messages]
    Q --> SM[Leer app.session_memory]
    SM --> R[Resolver pregunta autocontenida]
    Q --> AR[app.agent_runs]
    R --> AR
    AR --> CP[LangGraph checkpoints]
    AR --> H[app.human_feedback]
    H --> M2[Nuevo mensaje de feedback]
    AR --> A[app.audit_events]
    AR --> M3[Respuesta o nueva propuesta SQL]
    M3 --> UM[Actualizar app.session_memory]
```

Una corrección HITL crea un mensaje y una revisión nuevos; no actualiza ni elimina la propuesta SQL
anterior. Esto permite reconstruir la conversación completa como en un chat persistente.

# Estructura de datos de negocio

La PoC genera datos sintéticos durante la inicialización de `axiz_business_data`. No descarga ni
utiliza datos reales, PII, PAN, CVV u otra información de tarjetahabientes.

| Objeto | Volumen aproximado |
|---|---:|
| Comercios | 250 |
| Transacciones | 250 000 |
| Periodo transaccional | 365 días |
| Contracargos | Aproximadamente 900, derivados de transacciones aprobadas |
| Ciudades | 11 |
| MCC | 13 |
| Canales | POS, ECOMMERCE, CONTACTLESS y QR |
| Marcas | DINERS, VISA, MASTERCARD y AMEX |

Los datos son determinísticos y reproducibles. Incluyen aprobaciones, rechazos, reversos,
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

## Flujo entre capas

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
AXIZ_CONTEXT_RESOLVER_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_INTENT_DOMAIN_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CONVERSATION_CONTEXT_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_SQL_GENERATOR_MODEL_PRESET=openai_gpt_5_6_terra_sql
AXIZ_RESULT_VERIFIER_MODEL_PRESET=ollama_gpt_oss_20b_reasoning
AXIZ_EXPLANATION_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CATALOG_ANSWER_MODEL_PRESET=ollama_qwen3_8b_structured
```

También puede configurarse todo localmente:

```dotenv
AXIZ_CONTEXT_RESOLVER_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_INTENT_DOMAIN_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_CONVERSATION_CONTEXT_MODEL_PRESET=ollama_qwen3_8b_structured
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

# Abstracción de motores

LangGraph, los agentes y `WorkflowNodes` ya no dependen de Psycopg ni de una clase concreta. Consumen el contrato `QueryEngine`:

```python
class QueryEngine(ABC):
    @property
    def capabilities(self) -> QueryEngineCapabilities: ...

    async def health(self) -> QueryEngineHealth: ...
    async def estimate_cost(self, sql: str, tables: list[str]) -> CostValidation: ...
    async def execute(self, sql: str) -> QueryResult: ...
    async def close(self) -> None: ...
```

La PoC registra actualmente `PostgresQueryEngine`, que implementa:

- `EXPLAIN (FORMAT JSON)` para estimación de costo.
- Transacciones `BEGIN READ ONLY`.
- `statement_timeout`.
- Medición de tamaño de relaciones.
- Límite y truncamiento de filas.
- Reintentos exponenciales únicamente para errores transitorios de conexión/interfaz.

La selección es parametrizable:

```dotenv
QUERY_ENGINE=postgres
QUERY_ENGINE_RETRY_ATTEMPTS=2
QUERY_ENGINE_RETRY_BASE_SECONDS=0.25
```

Para incorporar Databricks SQL, Snowflake, Fabric Warehouse o Redshift se implementa otro adaptador y se registra en `QueryEngineFactory`. El grafo, los agentes, HITL y la memoria no se modifican. Cada motor debe declarar su dialecto y capacidades; el validador SQL y el generador usan el dialecto efectivo del motor, no un valor hardcodeado en LangGraph.

# Validación activa del catálogo de modelos

`ModelCatalogValidator` valida los perfiles efectivos después de aplicar presets y variables de entorno. Agrupa agentes que usan el mismo proveedor, base URL y modelo para no repetir probes innecesarios.

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

Para OpenAI o gateways compatibles se consulta el modelo y se prueba `responses.parse` con un contrato Pydantic mínimo. Para Ollama se consulta `/api/show` y luego `/api/chat` con JSON Schema. Un alias privado que no aparezca en el catálogo se acepta con estado `warning` cuando el probe estructurado funciona.

El reporte contiene por modelo único:

- proveedor y modelo;
- disponibilidad en catálogo;
- soporte efectivo de salida estructurada;
- contexto reportado o configurado;
- latencia del probe;
- warnings y error normalizado.

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

Capacidades incluidas:

- `Idempotency-Key` en creación de runs y feedback HITL.
- Índices únicos parciales para evitar duplicados.
- Un run concurrente por sesión y límite configurable por usuario.
- Reclamo atómico de un run `awaiting_approval` antes de reanudar LangGraph.
- Heartbeat que extiende el lease durante llamadas largas.
- Recuperación al iniciar de runs `running` cuyo lease expiró.
- `version` incrementado en cada transición persistida.
- Cancelación de runs activos y cancelación inmediata de un HITL pendiente.
- Semáforo por proceso para limitar llamadas LLM simultáneas.
- Reintentos de conexión del motor solo para fallos transitorios.

Configuración:

```dotenv
RUN_LEASE_SECONDS=360
RUN_LEASE_HEARTBEAT_SECONDS=30
MAX_CONCURRENT_RUNS_PER_USER=2
MAX_CONCURRENT_LLM_CALLS=8
```

El cliente Streamlit genera una clave de idempotencia por acción y también la envía en el header. Consumidores externos pueden fijarla explícitamente para repetir con seguridad una solicitud después de un timeout de red.

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

## Visualización en Streamlit

Cada propuesta y cada respuesta SQL muestran el panel **Validación previa a la aprobación y ejecución** con:

- estado de seguridad y costo;
- tipo de sentencia y límite aplicado;
- fuentes y columnas detectadas;
- filtros, esquemas y funciones bloqueadas;
- violaciones o advertencias;
- costo del planner frente al límite;
- filas estimadas de salida y máximo de filas procesadas por un nodo;
- cantidad de nodos y tamaño de relaciones;
- plan de ejecución tabular.

La pestaña **Plan de ejecución** contiene únicamente nodos de PostgreSQL: paso, operación, relación física, método de scan/join, filas, ancho, costo y filtro o condición. Las filas devueltas por el SQL se muestran en **Resultado de la consulta**, fuera del panel; nunca se reutilizan como si fueran el plan. El JSON de `EXPLAIN` permanece disponible dentro de **JSON técnico de EXPLAIN** para diagnóstico avanzado.

`EXPLAIN` no ejecuta la consulta. `Total Cost` es una unidad relativa del planner, no segundos ni dinero. `Plan Rows` y `Plan Width` son estimaciones que también alimentan la proyección de tokens posterior a la aprobación.

Durante el streaming se muestran los resultados resumidos de ambas etapas. La sección **Actividad y decisiones del agente** conserva una traza auditable, pero el panel de validación es independiente y permanece visible al reabrir una sesión.

# Consumo LLM

Los tokens se muestran en dos secciones separadas de la validación SQL:

- **Consumo LLM ejecutado:** uso real de llamadas que ya ocurrieron.
- **Estimación LLM si apruebas este SQL:** proyección de las llamadas que todavía no se ejecutaron.

La **validación de seguridad y costo** decide si una consulta puede ejecutarse contra la base; no consume tokens porque SQLGlot, `EXPLAIN` y PostgreSQL son tools determinísticas.

Para cada llamada se registran:

- agente, proveedor y modelo;
- entrada estimada antes de invocar el proveedor;
- salida máxima reservada mediante `max_output_tokens` o `num_predict`;
- máximo estimado de la llamada;
- tokens reales de entrada, salida y total;
- tokens de entrada cacheados, cuando OpenAI los reporta;
- tokens de razonamiento, cuando OpenAI los reporta;
- duración, cantidad de intentos y estado de la llamada.

La estimación previa utiliza el presupuesto conservador de `PromptBudget` después de aplicar la política de contexto. `reserved_output_tokens` es un **límite máximo**, no una predicción de que el modelo consumirá todos esos tokens. El consumo real reportado por el proveedor es la cifra autoritativa:

- OpenAI Responses API: `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens`, detalles de caché y razonamiento.
- Ollama native API: `prompt_eval_count` y `eval_count`; el total se calcula sumando ambos.

El acumulado se conserva durante todo el ciclo HITL. Si el usuario pide cambios, la nueva llamada al generador SQL se agrega al mismo run; después de aprobar, también se agregan las llamadas de verificación y explicación. La pregunta de capacidades resuelta determinísticamente no consume tokens y no muestra el panel.

Streamlit presenta **Consumo LLM ejecutado** con llamadas, entrada, salida y total reales. En el HITL muestra además **Estimación LLM si apruebas este SQL**. Para el flujo analítico normal se proyectan dos llamadas pendientes:

1. `result_verifier`, con hasta 20 filas de muestra.
2. `explanation`, con hasta 100 filas de muestra.

La entrada futura se aproxima con la pregunta, interpretación, SQL, columnas, `Plan Rows`, `Plan Width` y los límites de muestra. La salida estimada es una expectativa conservadora; `maximum_total_tokens` usa el `max_output_tokens` configurado y representa un techo, no una predicción. La UI muestra también **Total proyectado del run**, calculado como consumo real acumulado más consumo adicional estimado.

Después de aprobar, `RunResponse.llm_usage` se actualiza con el consumo autoritativo reportado por el proveedor. `RunResponse.llm_approval_estimate` conserva la estimación previa para comparar proyección y ejecución. Durante SSE se muestran ambos hitos y las métricas reaparecen al abrir una conversación anterior.

Esta sección mide tokens, no costo monetario. Para calcular dinero se necesitaría mantener una tabla de precios versionada por proveedor/modelo y aplicar precios distintos a entrada, salida y caché.

# Multiagente y herramientas

Los agentes LLM producen contratos Pydantic estructurados. Las tools ejecutan operaciones determinísticas y no consumen tokens del modelo. Los dominios y reglas de negocio provienen del catálogo semántico; no se codifican dentro de los agentes.

## Agentes

| Agente | Entrada | Salida | Descripción breve |
|---|---|---|---|
| **Context Resolver Agent** (`ContextResolverAgent`) | Pregunta actual, `ConversationMemory` y últimos turnos acotados | `ContextResolutionOutput`: pregunta original, pregunta autocontenida, indicador de follow-up, campos heredados, confianza y aclaración opcional | Detecta instrucciones elípticas como “ahora solo Lima” o “y por canal”. Reescribe la solicitud antes del routing sin generar SQL ni inventar filtros; si falta memoria suficiente, solicita aclaración. |
| **Intent & Domain Agent** (`IntentDomainAgent`) | `question`, dominios publicados y últimas entradas de `conversation_history` | `IntentDomainOutput`: intención, dominio, confianza, justificación resumida y pregunta de aclaración | Clasifica la solicitud como analítica, catálogo, capacidades, seguimiento de sesión o fuera de alcance. Las preguntas explícitas de capacidades y contexto pueden resolverse determinísticamente. |
| **Conversation Context Agent** (`ConversationContextAgent`) | Pregunta sobre la sesión, `ConversationMemory` como fuente primaria y últimos turnos como soporte | `ConversationAnswerOutput`: respuesta, turnos referenciados y advertencias | Responde preguntas como “¿qué datos te pedí?”, “¿qué SQL ejecutaste?” o “¿qué resultado dio?” sin consultar la base. Los casos explícitos se resuelven de forma determinística; solo referencias ambiguas usan el LLM configurado. |
| **Semantic Explorer Agent** (`SemanticExplorerAgent`) | `question`, `domain` | Diccionario de contexto: definición del dominio, `catalog_hits`, `allowed_sources`, `query_policy` y `selected_examples` | Especialista basado en tools que recupera contratos semánticos y ejemplos. No genera SQL ni llama directamente a la base. |
| **SQL Generator Agent** (`SqlGeneratorAgent`) | Pregunta autocontenida, `semantic_context`, `ConversationMemory`, historial, feedback HITL, SQL anterior y `max_allowed_rows` | `SqlGenerationOutput`: SQL, interpretación, supuestos, métricas, dimensiones, filtros, periodo y fuentes | Genera una única consulta read-only usando exclusivamente fuentes, métricas y joins publicados. El feedback humano es obligatorio; después de la llamada, `SqlFeedbackApplier` verifica y fuerza cambios estructurales soportados. |
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
| **SQL Feedback Applier** (`SqlFeedbackApplier`) | SQL regenerado, comentario HITL, dialecto y `MAX_RESULT_ROWS` | `SqlFeedbackApplication`: SQL corregido, límite solicitado/aplicado/anterior, indicador de cambio y advertencias | Detecta instrucciones como “sube el límite a 400” y modifica el AST después del LLM. Si el valor supera el máximo gobernado, lo limita de forma explícita y registra una advertencia. |
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
| Microsoft 365 Agents SDK | Canal opcional de Microsoft Teams |
| Docker Compose | Entorno local reproducible |
| Pytest | Tests unitarios e integración |

# Exportación Excel gobernada

La exportación es una **tool**, no un agente adicional. No requiere razonamiento ni una llamada al LLM: opera sobre un resultado ya aprobado por HITL, validado por SQLGlot/costo y ejecutado con el rol de solo lectura.

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

El archivo se construye a partir del resultado persistido del run; la exportación no vuelve a ejecutar SQL ni amplía el alcance aprobado. La UI usa generación diferida de Streamlit: el endpoint se invoca cuando el usuario pulsa **Exportar Excel**, por lo que preparar y descargar ocurre en un solo clic y no se precargan archivos al abrir conversaciones históricas. `ApiClient.download_excel(run_id)` devuelve directamente los bytes del XLSX; `export_excel(run_id)` se conserva por compatibilidad. Si el resultado fue truncado, el comportamiento recomendado es refinar la pregunta o los filtros antes de exportar.

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

La interfaz usa PostgreSQL como fuente de verdad del historial y presenta una navegación similar a
ChatGPT:

- Botón **Nuevo chat** que crea y selecciona inmediatamente una conversación vacía.
- Chats agrupados en `Hoy`, `Ayer`, `Últimos 7 días`, `Últimos 30 días` y `Anteriores`.
- Chat actual resaltado y repetido como título principal para evitar ambigüedad.
- Menú `⋯` por conversación para renombrar o eliminar.
- Búsqueda por título y recuperación automática del HITL pendiente.
- Eliminación del historial, runs, feedback y checkpoints asociados al chat.

PostgreSQL persiste:

- Sesiones y títulos.
- Preguntas del usuario.
- Propuestas SQL para HITL.
- Aprobaciones, rechazos y solicitudes de cambio.
- Cada nueva versión de una consulta corregida.
- Respuesta, tabla, especificación de gráfico, SQL y advertencias.
- Traza explicable de decisiones y herramientas.

Una revisión corregida se agrega como un turno nuevo; no reemplaza la propuesta anterior. El formulario
HITL usa `clear_on_submit`, por lo que **Cambios solicitados** queda vacío después de enviar una
decisión y no reaparece el comentario anterior en la siguiente revisión.

Durante la ejecución, `POST /api/v1/agent/runs/stream` transmite eventos SSE por cada etapa del grafo:
clasificación, exploración semántica, generación SQL, seguridad, costo, estimación de tokens, revisión, ejecución, verificación y explicación. La UI actualiza un panel de progreso y muestra la respuesta gradualmente. El flujo HITL
se reanuda por `POST /api/v1/agent/runs/{runId}/feedback/stream`.

## Aplicación determinística del feedback HITL

Cuando el usuario selecciona **Solicitar cambios**, el generador recibe el comentario, el SQL anterior y el máximo permitido. Para cambios semánticos —filtros, dimensiones, periodo o agregaciones— el LLM regenera la consulta. Para cambios estructurales reconocibles, el resultado se comprueba y modifica determinísticamente antes de volver a ejecutar seguridad y costo.

El primer cambio soportado es `LIMIT`:

```text
SQL anterior: LIMIT 200
Feedback: “sube el límite a 400”
SQL regenerado por el LLM: LIMIT 200
SqlFeedbackApplier: LIMIT 400
SqlSecurityValidator: confirma que 400 <= MAX_RESULT_ROWS
```

La transformación usa SQLGlot y no reemplazo de strings. Se reconocen expresiones como `límite a 400`, `limit 400`, `top 400`, `devuelve 400 filas` o `400 resultados como máximo`. La interpretación se sincroniza con el límite efectivo para evitar que la UI muestre `200` cuando el SQL ya contiene `400`.

Si se solicita un valor superior al máximo gobernado:

```text
MAX_RESULT_ROWS=500
Feedback: “cambia el límite a 900”
Resultado efectivo: LIMIT 500
Advertencia: el límite solicitado supera la política configurada.
```

Después de aplicar el cambio, el flujo vuelve a ejecutar `SqlSecurityValidator`, `EXPLAIN`, evaluación de costo y estimación de tokens antes de presentar la nueva revisión HITL.

## Jerarquía visual de cada respuesta

Antes de aprobar, la respuesta visible presenta únicamente la **interpretación**, el **SQL propuesto** y una sola línea compacta con modelos, tokens usados, llamadas y estimación posterior a la aprobación. La explicación funcional y los controles técnicos permanecen colapsados para mantener los botones HITL cerca del SQL.

Después de ejecutar, la prioridad se invierte:

- **Resultado y visualización** queda abierto por defecto y contiene respuesta, hallazgos, gráfico, tabla y exportación.
- **SQL ejecutado** queda colapsado.
- La línea de modelo/tokens continúa en formato compacto, sin tarjeta ni métricas grandes.
- **Qué hace esta consulta** y **Detalles avanzados** permanecen cerrados.

Las respuestas sin SQL —capacidades, catálogo y preguntas sobre la sesión— se muestran como mensajes normales; no renderizan “Qué hace esta consulta”, controles SQL ni un panel de resultado artificial. El encabezado de la aplicación es `Reporteria agentica SQL con HITL`.

La UI requiere `streamlit>=1.52` para usar la generación diferida de `st.download_button`. Todos los componentes migraron de `use_container_width` a `width="stretch"` o `width="content"`, eliminando las advertencias de deprecación.

## Memoria conversacional estructurada

La fuente primaria de contexto analítico es `app.session_memory`, no una concatenación libre de mensajes. La tabla mantiene un documento JSONB versionado por sesión:

```text
ConversationMemory
├── last_user_request
├── last_resolved_question
├── last_interpretation
├── last_domain
├── last_metrics[]
├── last_dimensions[]
├── last_filters[]
├── last_time_window
├── last_sql
├── last_result_schema[]
├── last_result_sample[]
├── last_row_count
├── last_answer
├── last_key_findings[]
├── last_models[]
├── last_token_usage
├── last_run_id / last_status
└── revision / updated_at
```

`ContextResolverAgent` se ejecuta antes de `IntentDomainAgent`. Comportamiento:

- Una pregunta autocontenida pasa sin consumir una llamada adicional.
- `Ahora solo Lima` hereda métrica, dimensiones y periodo de la última consulta, pero agrega únicamente el filtro solicitado.
- `Y por canal` conserva el contexto relevante y produce una pregunta autocontenida agrupada por canal.
- `Compáralo con el mes anterior` hereda la métrica y añade la comparación temporal.
- Una referencia sin memoria previa devuelve `needs_clarification`; no genera ni ejecuta SQL.
- Las preguntas de metaconversación, como `¿qué datos te pedí?`, se mantienen como `conversation_question` y se responden desde `ConversationMemory` sin consultar `business_data`.

`StructuredConversationMemoryService` actualiza la memoria al persistir una propuesta SQL y nuevamente después de la ejecución. `SqlMemoryExtractor` analiza el SQL validado con SQLGlot para complementar filtros y expresiones temporales. La muestra del resultado está limitada por:

```dotenv
CONVERSATION_MEMORY_RESULT_SAMPLE_ROWS=5
```

La memoria nunca almacena el dataset completo. Por defecto conserva como máximo cinco filas de una fuente ya autorizada por la capa semántica. El historial textual de `app.chat_messages` continúa siendo la fuente de auditoría y reconstrucción visual, pero solo actúa como soporte del resolver. Cada conversación tiene su propia fila y `ON DELETE CASCADE` evita mezclar o dejar memoria huérfana.

## Trazabilidad y razonamiento visible

La opción **Mostrar actividad del agente** presenta una traza persistida con:

- Intención y dominio seleccionados.
- Número de contratos y ejemplos recuperados.
- Métricas, dimensiones, fuentes y supuestos utilizados.
- Revisión SQL generada.
- Resultado de SQLGlot.
- Costo, filas y tamaño estimados por PostgreSQL.
- Filas devueltas, latencia y truncamiento.
- Confianza, observaciones y advertencias de la verificación.

Esta traza es una explicación operativa y auditable. No guarda ni muestra tokens ocultos, razonamiento
privado o chain-of-thought interno del modelo.

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

No se modifica código, LangGraph ni los agentes. La base externa debe publicar las vistas referenciadas
por `semantic_catalog`, usar el dialecto configurado y conceder únicamente `CONNECT`, `USAGE` sobre
el esquema semántico y `SELECT` sobre vistas autorizadas. El agente añade `BEGIN READ ONLY`, timeout,
validación SQLGlot y límites de costo.

Los certificados colocados en `infrastructure/certs/` se montan como solo lectura en `/app/certs`.
No se deben versionar certificados productivos ni llaves privadas. En producción, las credenciales deben
inyectarse desde un secret manager.

`GET /health/ready` devuelve `business_data_mode` junto con el estado de ambas bases.

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

# Actualización desde versiones con `axiz_sql_agent`

Las versiones iniciales crearon una sola base llamada `axiz_sql_agent`. Desde `0.4.2` no es obligatorio eliminar el volumen: `postgres-bootstrap` detecta el clúster existente, crea `axiz_agent_control` y `axiz_business_data`, aplica sus esquemas y genera el dataset embebido.

```bash
make down
make up
```

La base antigua `axiz_sql_agent` se conserva para evitar una eliminación automática de información. Las conversaciones anteriores no se copian por defecto. Para migrar el esquema `app` después de que el bootstrap haya creado `axiz_agent_control`:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  exec -T postgres pg_dump -U app_owner -d axiz_sql_agent \
  --data-only --schema=app --no-owner --no-privileges \
  > app-sessions-backup.sql

docker compose --env-file .env -f infrastructure/docker-compose.yml \
  exec -T postgres psql -U app_owner -d axiz_agent_control \
  < app-sessions-backup.sql
```

Los checkpoints HITL pendientes deben cerrarse antes de la migración; no se recomienda trasladarlos parcialmente entre bases durante la PoC. Después de verificar la información, la base anterior puede retirarse manualmente.

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

## Suite de integración de PostgreSQL

Primero levantar Docker y luego ejecutar:

```bash
TEST_CONTROL_DSN=postgresql://app_owner:app_owner@localhost:5432/axiz_agent_control \
TEST_AGENT_DSN=postgresql://agent_reader:agent_readonly@localhost:5432/axiz_business_data \
pytest tests/integration -q
```

| Test | Qué valida |
|---|---|
| `test_semantic_dataset_contains_realistic_volume` | Más de 200 000 filas visibles y 250 comercios |
| `test_semantic_views_cover_payments_declines_and_chargebacks` | Datos en vistas de pagos, rechazos y contracargos |
| `test_agent_role_cannot_modify_or_read_internal_layers` | Bloqueo físico de operational, analytics y CREATE |
| `test_agent_connection_is_isolated_to_business_data_database` | El rol se conecta a `axiz_business_data` y no puede consultar el esquema `app` |
| `test_control_database_contains_conversation_tables_only` | La base de control contiene tablas `app` y no contiene el esquema `semantic` |

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
- PostgreSQL, local o externo, es la única implementación incluida de `QueryEngine`. Otro motor requiere un adaptador y registro en la fábrica, no cambios en los agentes ni en el grafo.
- Los modelos Ollama grandes requieren dimensionar RAM/VRAM y contexto; el perfil de 30B no es adecuado
  para todos los equipos de desarrollo.
- La integración Teams necesita registro real en Entra ID para una prueba end-to-end.


## UX compacta y memoria de sesión 0.4.9

- La tarjeta grande de modelos fue reemplazada por una línea compacta con elipsis y tooltip.
- Antes del HITL, interpretación y SQL propuesto permanecen visibles.
- Después de ejecutar, **Resultado y visualización** queda abierto y **SQL ejecutado** colapsado.
- Respuestas sin SQL no muestran secciones específicas de consultas.
- Se añadió `Intent.CONVERSATION_QUESTION` y `ConversationContextAgent`.
- El historial de contexto se enriquece desde `chat_messages.metadata.payload` con información acotada del run.
- Preguntas explícitas sobre la solicitud anterior se resuelven determinísticamente, sin costo LLM.
- Las referencias conversacionales ambiguas usan el perfil `conversation_context` configurado en `config/agents.yaml`.


## Memoria conversacional estructurada 0.5.0

- Se añadió `app.session_memory` con JSONB, revisión y referencia al último run.
- Se añadió `ConversationMemoryRepository` con validación de ownership y upsert versionado.
- Se añadió `ContextResolverAgent` antes de la clasificación de intención.
- El workflow usa `resolved_question` para catálogo, SQL, verificación y explicación.
- `SqlGenerationOutput` registra filtros y ventana temporal estructurados.
- `SqlMemoryExtractor` completa filtros y fechas desde el SQL validado.
- `ConversationContextAgent` consulta primero la memoria estructurada y usa el historial solo como soporte.
- La memoria limita la muestra de resultados mediante `CONVERSATION_MEMORY_RESULT_SAMPLE_ROWS`.
- La actualización es idempotente sobre volúmenes existentes; no requiere `docker compose down -v`.



## Corrección de persistencia de leases 0.6.1

- Se eliminó el predicado ambiguo `(:lease_owner IS NULL OR lease_owner=:lease_owner)` de `RunRepository.update`.
- Cuando existe un lease, el repositorio usa `lease_owner = CAST(:lease_owner AS varchar)`; cuando no existe, omite por completo el parámetro.
- La propuesta `awaiting_approval`, los estados terminales y los errores vuelven a persistirse después del streaming.
- Streamlit conserva y muestra el error técnico tras el `rerun` cuando el backend no logra guardar un mensaje, evitando una conversación aparentemente vacía.
- Se añadieron pruebas de regresión para updates con y sin propietario de lease.

## Abstracción, validación de modelos y resiliencia 0.6.0

- `QueryEngine` desacopla el workflow de Psycopg y publica capacidades/dialecto.
- `PostgresQueryEngine` implementa salud, costo y ejecución read-only con retry transitorio.
- `ModelCatalogValidator` consulta catálogo y prueba Structured Outputs para cada modelo único.
- Readiness expone motor y reporte de validación de modelos.
- Runs y feedback aceptan `Idempotency-Key`.
- PostgreSQL mantiene versión, lease, heartbeat, cancelación y claves únicas parciales.
- Advisory locks serializan decisiones de concurrencia por usuario.
- `RunExecutionCoordinator` renueva leases y observa cancelaciones.
- Se limita la concurrencia de runs por usuario y de llamadas LLM por proceso.
- La actualización del esquema es idempotente y no exige eliminar volúmenes.

## Corrección de feedback estructural 0.6.2

- El feedback `request_changes` continúa pasando por `SqlGeneratorAgent`, pero ya no depende exclusivamente de la obediencia del LLM.
- `SqlFeedbackApplier` detecta y aplica cambios de `LIMIT` sobre el AST del SQL regenerado.
- `SqlGeneratorAgent` recibe `max_allowed_rows` y una instrucción explícita de no conservar el límite anterior cuando el usuario solicita otro.
- La interpretación se sincroniza con el límite efectivo y las advertencias se incorporan a los supuestos visibles.
- Solicitudes por encima de `MAX_RESULT_ROWS` se acotan al máximo gobernado.
- El SQL corregido vuelve a pasar por seguridad, costo, plan y HITL antes de ejecutarse.
- Se añadieron pruebas para aumentar, agregar, limitar y no modificar `LIMIT`, además del wiring en `ApplicationContainer` y `WorkflowNodes`.
