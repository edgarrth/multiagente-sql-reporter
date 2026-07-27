# Multiagente de reporteria agentica

PoC multiagente de **reportería agéntica SQL con HITL**. Convierte preguntas en lenguaje natural en consultas SQL de solo lectura, utiliza una capa semántica gobernada, valida seguridad y costo antes de ejecutar y solicita aprobación humana.

# Inicio rápido

## Requisitos

- Docker y Docker Compose.
- Una API key para el proveedor OpenAI-compatible configurado, o modelos disponibles en Ollama.
- Puertos locales disponibles: `8000`, `8501`, `5432` y `6379`.

## Configuración

```bash
cp .env.example .env
```

Configurar como mínimo:

```dotenv
OPENAI_API_KEY=<api-key>
OPENAI_BASE_URL=https://api.openai.com/v1
APP_SECRET_KEY=<mínimo-32-caracteres>
BOOTSTRAP_PASSWORD=<contraseña-segura>
INTERNAL_SERVICE_KEY=<service-key-segura>
```

## Ejecución

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

El arranque normal conserva los volúmenes:

```bash
make down
make up
```

Para eliminar sesiones, auditoría, caché y datos sintéticos:

```bash
make reset
make up
```

# Arquitectura

```mermaid
flowchart LR
    U[Usuario] --> ST[Streamlit]
    ST -->|JWT + SSE| API[FastAPI]
    API --> LG[LangGraph Workflow]

    LG --> CR[Context Resolver]
    LG --> ID[Intent & Domain]
    LG --> SE[Semantic Explorer]
    LG --> FI[Feedback Interpreter]
    LG --> SG[SQL Generator]
    LG --> FC[Feedback Compliance]
    LG --> RV[Result Verifier]
    LG --> EA[Explanation]

    CR --> MR[Agent Model Registry]
    ID --> MR
    FI --> MR
    SG --> MR
    FC --> MR
    RV --> MR
    EA --> MR

    MR --> OA[OpenAI-compatible API]
    MR --> OL[Ollama API]

    SE --> CAT[YAML Semantic Catalog]
    LG --> SEC[SQLGlot Guardrails]
    LG --> COST[Query Engine / EXPLAIN]
    LG --> HITL[Human SQL Review]
    LG --> EXEC[Read-only Query Engine]

    API --> CTRL[(axiz_agent_control)]
    EXEC --> DATA[(axiz_business_data)]
    API --> RD[(Redis)]
    API --> XLSX[Excel Export Tool]
```

Responsabilidades:

```text
Control plane
├── usuarios y sesiones
├── mensajes y memoria estructurada
├── runs, feedback y auditoría
└── checkpoints LangGraph

Data plane
├── operational
├── analytics
└── semantic

Workflow
├── clasificación contextual
├── interpretación y generación
├── validaciones determinísticas
├── HITL
└── ejecución y explicación
```

# Clasificación contextual y memoria

## Clasificación contextual semántica

`ContextResolverAgent` clasifica cada mensaje por su relación con la conversación. No utiliza vocabulario específico del dominio para decidir si una solicitud es un seguimiento.

| Relación | Comportamiento |
|---|---|
| `independent_request` | La solicitud es autocontenida y continúa al routing normal |
| `analytical_follow_up` | Modifica o amplía una consulta previa y debe producir una nueva propuesta SQL |
| `session_reference` | Pregunta por la sesión, SQL, resultado o consumo anterior; se responde desde memoria sin ejecutar SQL |
| `ambiguous` | No puede resolverse con seguridad y se solicita aclaración |

```mermaid
flowchart TD
    Q[Mensaje] --> CR[Context Resolver]
    CR -->|independent_request| ID[Intent & Domain]
    CR -->|analytical_follow_up| FI[Feedback Interpreter]
    CR -->|session_reference| CA[Conversation Context]
    CR -->|ambiguous| CL[Solicitar aclaración]

    FI --> PLAN[SqlFeedbackPlan]
    PLAN --> GEN[Aplicación AST o regeneración]
    GEN --> COMP[Validar cumplimiento]
    COMP --> SEC[Seguridad]
    SEC --> COST[Costo]
    COST --> HITL[Nueva aprobación]
```

Reglas:

- Un seguimiento analítico posterior a una ejecución siempre genera una nueva propuesta SQL.
- La nueva propuesta vuelve a seguridad, costo y HITL.
- Las preguntas sobre la sesión no consultan `business_data`.
- La memoria estructurada es la fuente de verdad; el historial textual solo ayuda a resolver referencias.
- Si la resolución no es segura, el sistema pide aclaración.

## Memoria estructurada

Cada sesión mantiene una fila versionada en `app.session_memory` con:

```text
solicitud original y autocontenida
interpretación y dominio
métricas y dimensiones
filtros y periodo
ordenamiento y límite
fuentes semánticas
SQL aprobado
esquema y muestra limitada del resultado
respuesta y hallazgos
modelos y tokens consumidos
último run y revisión
```

No se almacena el dataset completo. La muestra se controla con:

```dotenv
CONVERSATION_MEMORY_RESULT_SAMPLE_ROWS=5
```

# Flujo del agente

```mermaid
flowchart TD
    Q[Pregunta] --> CR[1. Clasificar relación contextual]
    CR -->|Referencia de sesión| CA[Responder desde memoria]
    CR -->|Ambigua| CL[Solicitar aclaración]
    CR -->|Nueva solicitud| ID[Clasificar intención y dominio]
    CR -->|Seguimiento| FI[Interpretar cambios]

    ID --> SE[Explorar catálogo]
    SE --> GEN[Generar SQL]
    FI --> PLAN[Crear SqlFeedbackPlan]
    PLAN --> GEN

    GEN --> APPLY[Aplicar cambios AST seguros]
    APPLY --> COMP[Validar cumplimiento]
    COMP -->|Reintentar| GEN
    COMP --> SEC[Validar seguridad]
    SEC -->|Reparar| GEN
    SEC --> COST[EXPLAIN y costo]
    COST --> TOK[Estimar tokens posteriores]
    TOK --> HITL[Revisar y aprobar SQL]

    HITL -->|Solicitar cambios| FI
    HITL -->|Rechazar| END[Fin]
    HITL -->|Aprobar| RUN[Ejecutar read-only]
    RUN --> VERIFY[Verificar resultado]
    VERIFY --> EXP[Explicar y visualizar]
    EXP --> MEM[Actualizar memoria]
    MEM --> XLSX[Exportación opcional]
```

# Correcciones de SQL

El feedback se interpreta como un plan tipado. La solución combina:

- LLM para cambios semánticos.
- SQLGlot para transformaciones estructurales seguras.
- Validación de cumplimiento para comprobar que se aplicó todo lo solicitado.
- Preservación de cláusulas no modificadas.

Cambios soportados:

```text
set_limit
add_filter
remove_filter
replace_filter
change_time_window
add_dimension
remove_dimension
change_grouping
change_order
add_metric
remove_metric
replace_metric
replace_source
semantic_regeneration
```

La propuesta corregida no llega a HITL si faltan cambios o si se alteraron elementos no solicitados.

# Persistencia y separación de bases

| Base | Propósito | Acceso del agente SQL |
|---|---|---|
| `axiz_agent_control` | Usuarios, sesiones, mensajes, memoria, runs, feedback, auditoría y checkpoints | Denegado |
| `axiz_business_data` | Datos sintéticos, modelo analítico y vistas semánticas | `SELECT` únicamente sobre `semantic` |

Configuración predeterminada:

```dotenv
DATABASE_URL=postgresql+psycopg://app_owner:app_owner@postgres:5432/axiz_agent_control
CHECKPOINT_DATABASE_URL=postgresql://app_owner:app_owner@postgres:5432/axiz_agent_control
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

Para producción puede externalizarse solamente el data plane:

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:password@db.example.com:5432/business_data?sslmode=verify-full
```

El workflow no cambia; la conexión efectiva se resuelve mediante `AGENT_DATABASE_URL`.

# Modelo de datos de negocio

## `operational`

Modelo cercano al origen transaccional. Permanece oculto al agente.

| Tabla | Grain |
|---|---|
| `operational.merchants` | Un comercio |
| `operational.payment_transactions` | Una transacción |
| `operational.chargebacks` | Un contracargo |

## `analytics`

Datos depurados y preparados para análisis.

| Tabla | Grain |
|---|---|
| `analytics.dim_date` | Un día |
| `analytics.dim_merchant` | Un comercio |
| `analytics.fact_payment_transactions` | Una transacción analítica |
| `analytics.fact_chargebacks` | Un contracargo analítico |

## `semantic`

Interfaz SQL gobernada consumida por el agente.

| Vista | Grain | Uso |
|---|---|---|
| `semantic.v_payment_transactions` | Una transacción | Detalle autorizado |
| `semantic.v_daily_payment_metrics` | Día + dimensiones | KPIs diarios |
| `semantic.v_merchant_performance` | Día + comercio | Desempeño de comercios |
| `semantic.v_monthly_payment_metrics` | Mes + dimensiones | Tendencias mensuales |
| `semantic.v_decline_analysis` | Día + dimensiones + respuesta | Rechazos |
| `semantic.v_chargeback_metrics` | Mes + dimensiones + motivo | Contracargos |

El rol `agent_reader` tiene acceso únicamente a vistas `semantic`, transacciones read-only y timeout configurado.

# Capa semántica y catálogo

| Elemento | Ubicación | Responsabilidad |
|---|---|---|
| Datos | PostgreSQL `operational` y `analytics` | Persistencia y preparación |
| Capa semántica SQL | PostgreSQL `semantic` | Vistas y métricas ejecutables |
| Catálogo semántico | `semantic_catalog/*.yaml` | Significado, grain, sinónimos, joins, ejemplos y políticas |

El catálogo se descubre dinámicamente:

```text
semantic_catalog/
├── global/
└── domains/<dominio>/
    ├── domain.yaml
    ├── entities/
    ├── metrics/
    ├── joins/
    ├── quality/
    ├── examples/
    └── trusted_queries/
```

Para publicar un dominio se crean sus vistas gobernadas y su contrato YAML; no se modifica el grafo.

# Agentes

| Agente | Input | Output | Responsabilidad |
|---|---|---|---|
| `ContextResolverAgent` | Pregunta, memoria y contexto acotado | `ContextResolutionOutput` | Clasifica la relación contextual y construye una solicitud autocontenida |
| `IntentDomainAgent` | Pregunta y dominios publicados | `IntentDomainOutput` | Clasifica intención y dominio |
| `ConversationContextAgent` | Pregunta sobre la sesión y memoria | `ConversationAnswerOutput` | Responde referencias a la conversación sin SQL |
| `SemanticExplorerAgent` | Pregunta y dominio | Contexto semántico | Recupera contratos, fuentes y ejemplos |
| `FeedbackInterpreterAgent` | Feedback, SQL anterior y catálogo | `SqlFeedbackPlan` | Descompone cambios libres o compuestos |
| `SqlGeneratorAgent` | Solicitud, contexto, memoria y plan | `SqlGenerationOutput` | Genera o regenera una consulta read-only |
| `FeedbackComplianceAgent` | Plan y SQL anterior/revisado | Cumplimiento semántico | Comprueba que la corrección mantenga el significado solicitado |
| `ResultVerifierAgent` | Pregunta, SQL y resultado | `VerificationOutput` | Verifica que el resultado responda la pregunta |
| `ExplanationAgent` | Resultado verificado | `ExplanationOutput` | Explica y propone visualización |

# Tools y servicios determinísticos

| Componente | Input | Output | Responsabilidad |
|---|---|---|---|
| `SemanticCatalogTool` | Consulta y dominio | Documentos y políticas | Fuente de verdad semántica |
| `ExampleSelectorTool` | Pregunta y dominio | Ejemplos NL-to-SQL | Selección de ejemplos |
| `StructuredConversationMemoryService` | Estado y respuesta | `ConversationMemory` | Actualización versionada de memoria |
| `SqlFeedbackPlanValidator` | Plan y catálogo | Plan normalizado | Valida símbolos y estrategia |
| `SqlFeedbackApplier` | SQL y plan | SQL transformado | Aplica cambios AST seguros |
| `SqlFeedbackComplianceValidator` | SQL anterior/final y plan | `FeedbackComplianceResult` | Verifica cambios y preservación |
| `SqlDialectNormalizer` | SQL generado | SQL canónico | Normaliza variantes equivalentes del dialecto antes del parsing |
| `SqlSecurityValidator` | SQL y políticas | `SecurityValidation` | Bloquea operaciones y fuentes no permitidas |
| `QueryEngine` | SQL | Costo, resultado y salud | Contrato neutral del motor |
| `PostgresQueryEngine` | SQL PostgreSQL | `EXPLAIN` y resultado | Implementación read-only |
| `ChartBuilderTool` | Resultado | Visualización | Selección determinística de tabla/gráfico |
| `ExcelExportTool` | Resultado persistido | XLSX | Exportación segura sin reejecutar SQL |
| `LLMUsageCollector` | Métricas del proveedor | Resumen de tokens | Consumo real por run |
| `LLMApprovalTokenEstimator` | SQL, costo y perfiles | Estimación futura | Tokens posteriores a la aprobación |
| `ModelCatalogValidator` | Perfiles efectivos | Reporte de modelos | Catálogo y probe estructurado |
| `RunExecutionCoordinator` | Run y lease | Context manager | Concurrencia, heartbeat y cancelación |

# Seguridad y costo

## Seguridad SQL

`SqlSecurityValidator` comprueba:

- Una sola sentencia.
- Solo `SELECT`.
- Fuentes incluidas en la allowlist.
- Esquemas y funciones bloqueadas.
- Ausencia de joins cartesianos.
- Filtro temporal cuando la política lo exige.
- Límite máximo de filas.
- Parsing con el dialecto efectivo.

Antes del AST se aplica una normalización conservadora de sintaxis equivalente del dialecto. La normalización no elimina sentencias, predicados, fuentes ni identificadores; SQLGlot sigue siendo la autoridad de seguridad.

## Costo

`PostgresQueryEngine.estimate_cost` ejecuta `EXPLAIN (FORMAT JSON)` y evalúa:

- Costo del planner.
- Filas del nodo raíz.
- Máximo de filas estimadas por cualquier nodo.
- Tamaño de relaciones físicas.
- Timeout.

Configuración:

```dotenv
MAX_RESULT_ROWS=500
MAX_PLAN_ROWS=250000
MAX_PLAN_COST=150000
MAX_RELATION_BYTES=536870912
SQL_TIMEOUT_SECONDS=20
MAX_SQL_REPAIR_ATTEMPTS=2
```

# Modelos por agente

Los perfiles viven en `config/agents.yaml`. Cada agente puede seleccionar proveedor, modelo, contexto, salida, razonamiento, timeout y reintentos.

Ejemplo:

```yaml
agents:
  context_resolver:
    preset: ${AXIZ_CONTEXT_RESOLVER_MODEL_PRESET:-openai_gpt_5_6_luna_routing}
  sql_generator:
    preset: ${AXIZ_SQL_GENERATOR_MODEL_PRESET:-openai_gpt_5_6_terra_sql}
```

La validación activa se configura con:

```dotenv
MODEL_VALIDATION_ON_STARTUP=true
MODEL_VALIDATION_MODE=probe
MODEL_VALIDATION_FAILURE_POLICY=warn
```

Endpoints:

```text
GET  /api/v1/models/validation
POST /api/v1/models/validation/refresh
```

Los aliases privados son válidos cuando el proveedor los resuelve y el probe estructurado funciona.

# Estructura del proyecto

```text
src/axiz/pe/sql_agent/
├── agents/
├── api/routes/
├── core/
├── models/
├── query_engines/
├── repositories/
├── services/
├── tools/
└── workflow/

config/
semantic_catalog/
streamlit_app/
infrastructure/
tests/
```

# Endpoints principales

| Método y ruta | Uso |
|---|---|
| `GET /health/live` | Proceso activo |
| `GET /health/ready` | Dependencias listas |
| `POST /api/v1/auth/login` | Login local |
| `POST /api/v1/sessions` | Crear conversación |
| `GET /api/v1/sessions` | Listar conversaciones |
| `PATCH /api/v1/sessions/{sessionId}` | Renombrar |
| `DELETE /api/v1/sessions/{sessionId}` | Eliminar |
| `GET /api/v1/sessions/{sessionId}/messages` | Recuperar historial |
| `POST /api/v1/agent/runs/stream` | Ejecutar con SSE |
| `POST /api/v1/agent/runs/{runId}/feedback/stream` | Reanudar HITL |
| `GET /api/v1/agent/runs/{runId}` | Consultar estado |
| `POST /api/v1/agent/runs/{runId}/cancel` | Cancelar |
| `GET /api/v1/agent/runs/{runId}/exports/excel` | Descargar Excel |
| `POST /api/v1/catalog/reload` | Recargar catálogo |

# Pruebas

```bash
pytest tests/unit -q
make test
```

La suite cubre contratos, modelos, catálogo, memoria, clasificación contextual, feedback generalizado, seguridad, motores, resiliencia, streaming, UI y exportación.

# Ejemplos

- ¿Cuál fue la tasa de aprobación de los últimos siete días por canal?
- ¿Qué comercios tuvieron mayor facturación el mes pasado?
- ¿Cómo evolucionó el monto procesado por MCC?
- Compara la facturación del último mes cerrado con el anterior por marca.
- ¿Cuáles fueron los principales motivos de contracargo de los últimos seis meses?
- ¿Qué SQL ejecutaste en la consulta anterior?
