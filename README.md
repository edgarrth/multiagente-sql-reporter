# Axiz SQL Agent PoC 0.9.0

Sociedad autónoma gobernada de agentes para analítica Text-to-SQL. El sistema convierte una
solicitud de negocio en un plan de investigación, delega tareas a especialistas configurables,
prepara evidencia en paralelo, solicita aprobación humana para cada consulta y sintetiza una
respuesta trazable a resultados SQL verificados.

La autonomía está limitada deliberadamente: los agentes pueden decidir **cómo investigar**, pero
no pueden decidir sus permisos, alterar presupuestos, omitir seguridad, saltarse el análisis de
costo, ejecutar SQL sin HITL ni modificar las políticas financieras.

# Evolución desde `agente-workflow-orquestado`

La rama `agente-workflow-orquestado` conserva la versión **0.7.4**, anterior al intento de convertir
la solución en una sociedad autónoma. Esa versión es un workflow gobernado y secuencial: cada
especialista LLM ejecuta una función acotada dentro de un único grafo central.

| Aspecto | `agente-workflow-orquestado` 0.7.4 | Sociedad autónoma 0.9.0 |
|---|---|---|
| Unidad principal | Workflow SQL central | Supervisor + planner + subgrafos especialistas |
| Delegación | Rutas predeterminadas | Selección dinámica de tareas y especialistas |
| Especialistas | Clases llamadas por nodos | Subgrafos LangGraph aislados por invocación |
| Paralelismo | No | Fan-out por olas mediante `Send` |
| Replanificación | Feedback del usuario | Supervisor y crítico pueden solicitar nueva evidencia |
| Límites | Principalmente por run/consulta | Globales y por tarea |
| Evidencia | Resultado principal | Ledger multi-evidencia y hallazgos con `evidence_ids` |
| Extensibilidad | Requiere cablear código | Perfil YAML + contratos semánticos |
| Caché | Caché técnica limitada | Redis para contexto, planes y propuestas reutilizables |
| Evaluación | Unitarias y contratos | Unitarias, integración, trayectoria agentic y runner E2E live |

La funcionalidad de 0.7.4 se conserva: clasificación contextual, memoria estructurada, catálogo
semántico, generación SQL, feedback generalizado, SQLGlot, `EXPLAIN`, HITL, ejecución read-only,
verificación, explicación, gráficos, Excel, SSE, idempotencia, leases, cancelación, OpenAI/Ollama,
Streamlit y Teams opcional.

# Decisión arquitectónica: autonomía con fronteras determinísticas

No todo debe convertirse en agente. En un contexto financiero, las decisiones que conceden
autoridad o materializan riesgo permanecen fuera del LLM.

## Autónomo

- Descomponer el objetivo en tareas de evidencia.
- Seleccionar especialistas habilitados.
- Preparar tareas independientes en paralelo.
- Formular hipótesis y preguntas analíticas.
- Reparar una propuesta dentro de límites de tarea.
- Solicitar evidencia adicional.
- Rechazar conclusiones insuficientes.
- Elegir cuándo la evidencia es suficiente.
- Sintetizar hallazgos citando evidencia.

## Determinístico y obligatorio

- Identidad, credenciales y roles PostgreSQL.
- Allowlist de fuentes y esquemas denegados.
- Parsing y políticas SQLGlot.
- Límites por tarea, investigación y usuario.
- `EXPLAIN`, costo, filas y tamaño de relaciones.
- HITL para cada SQL que pueda ejecutarse.
- Ejecución read-only mediante `QueryEngine`.
- Idempotencia, leases, heartbeat y cancelación.
- Auditoría, exportación y protección contra fórmulas Excel.

Esta separación permite autonomía útil sin entregar a los agentes el control de seguridad,
regulación o gasto.

# Arquitectura

```mermaid
flowchart TD
    U[Usuario] --> UI[Streamlit / Teams]
    UI --> API[FastAPI + SSE]
    API --> CR[Context Resolver]
    CR --> ID[Intent y contexto]
    ID --> PARENT[LangGraph: Autonomous Society Parent Graph]

    PARENT --> SUP[Supervisor autónomo]
    SUP --> PLAN[Planner]
    PLAN --> DISP[Dispatcher gobernado]

    DISP -->|Send / ola paralela| SA1[Subgrafo especialista A]
    DISP -->|Send / ola paralela| SA2[Subgrafo especialista B]
    DISP -->|Send / ola paralela| SAN[Subgrafo especialista N]

    subgraph SG[Subgrafo especialista por invocación]
        PREP[Preparar tarea] --> CAT[Explorar catálogo]
        CAT --> SQL[Generar o revisar SQL]
        SQL --> SEC[SQLGlot]
        SEC --> COST[EXPLAIN y presupuesto]
        COST --> SELF[Auto-revisión limitada]
        SELF --> PROP[Propuesta de consulta]
    end

    SA1 -. implementa .-> SG
    SA2 -. implementa .-> SG
    SAN -. implementa .-> SG

    PROP --> Q[Cola de propuestas]
    Q --> HITL[HITL obligatorio]
    HITL --> EXEC[QueryEngine read-only]
    EXEC --> VER[Verificador de resultado]
    VER --> LEDGER[Ledger inmutable de evidencia]
    LEDGER --> CRIT[Subgrafo crítico]
    CRIT --> SUP
    SUP -->|finalizar| SYN[Síntesis con evidence_ids]
    SYN --> UI

    CAT --> CACHE[(Redis Agent Cache)]
    PLAN --> CACHE
    CR --> CACHE

    PARENT --> CTRL[(axiz_agent_control)]
    EXEC --> DATA[(axiz_business_data / semantic)]
```

# Cómo se usa LangGraph

LangGraph se utiliza en dos niveles:

1. **Grafo padre:** mantiene el estado durable de la investigación, supervisor, olas paralelas,
   propuestas, HITL, evidencia, crítico y síntesis.
2. **Subgrafos especialistas:** cada perfil se compila como un subgrafo reutilizable y se invoca con
   estado aislado. El subgrafo no recibe autoridad para ejecutar SQL.

La versión 0.7.4 usaba LangGraph principalmente como máquina de estados de un workflow. La 0.9.0 lo
usa además como runtime multiagente: delegación dinámica, subgrafos, fan-out con `Send`, reducers para
resultados paralelos, interrupciones HITL y reanudación durable.

# Flujo end-to-end

```mermaid
flowchart TD
    Q[Solicitud] --> C[Clasificación contextual]
    C --> PL[Planner crea plan mínimo]
    PL --> S[Supervisor selecciona tareas listas]
    S --> F[Fan-out paralelo acotado]
    F --> SP[Subgrafos especialistas]
    SP --> COL[Recolectar propuestas]
    COL --> H[Procesar propuestas una por una con HITL]
    H -->|Aprobar| E[Ejecutar read-only]
    H -->|Cambios| R[Feedback semántico generalizado]
    H -->|Rechazar| D[Descartar propuesta]
    R --> H
    E --> V[Verificar y registrar evidencia]
    D --> MORE{¿Quedan propuestas?}
    V --> MORE
    MORE -->|Sí| H
    MORE -->|No| CR[Crítico]
    CR --> S
    S -->|Nueva evidencia| F
    S -->|Finalizar| SYN[Síntesis grounded]
```

Una propuesta recuperada de caché **no** evita controles: vuelve a pasar por seguridad, costo,
auto-revisión y HITL. Si se regenera, pierde su procedencia de caché y solo puede volver a
almacenarse después de superar los gates actuales.

# Agentes y equivalencia con las capacidades anteriores

| Agente | Entrada | Salida | Descripción breve |
|---|---|---|---|
| Context Resolver | Mensaje, `ConversationMemory`, historial acotado | `ContextResolutionOutput` | Clasifica dependencia y resuelve follow-ups sin generar SQL |
| Intent & Domain | Pregunta y dominios publicados | `IntentDomainOutput` | Separa intención y dominio |
| Investigation Planner | Objetivo, especialistas, catálogo y presupuesto | `InvestigationPlan` | Crea el plan mínimo, dependencias y prioridades |
| Autonomous Supervisor | Plan, evidencia, crítico y consumo | `SupervisorDecision` | Selecciona olas, crea tareas, rechaza conclusiones o finaliza |
| Domain Specialist | `InvestigationTask`, memoria y evidencia previa | `SpecialistTaskOutput` | Prepara una tarea dentro de un subgrafo aislado |
| Semantic Explorer | Pregunta refinada y dominio | Contexto semántico | Recupera contratos, políticas y ejemplos |
| SQL Generator | Pregunta, catálogo, memoria y feedback | `SqlGenerationOutput` | Genera o repara SQL |
| Feedback Interpreter | Comentario, SQL anterior y contrato | `SqlFeedbackPlan` | Descompone cambios semánticos |
| Feedback Compliance | Plan y SQL anterior/revisado | `FeedbackComplianceResult` | Verifica cambios e invariantes |
| Result Verifier | Pregunta, SQL y `QueryResult` | `VerificationOutput` | Valida suficiencia del resultado |
| Critic | Plan y ledger de evidencia | `CriticReviewOutput` | Detecta contradicciones y faltantes |
| Explanation / Synthesis | Evidencia verificada | `AutonomousSynthesisOutput` | Produce hallazgos enlazados por `evidence_ids` |

| Tool | Entrada | Salida | Descripción breve |
|---|---|---|---|
| Semantic Catalog | Dominio y búsqueda | Contratos y allowlist | Fuente de verdad semántica YAML |
| SQL Feedback Applier | SQL + `SqlFeedbackPlan` | SQL transformado | Aplica cambios AST seguros |
| SQL Security Validator | SQL + políticas | `SecurityValidation` | Bloquea DML/DDL, fuentes y joins no permitidos |
| Query Engine | SQL validado | `CostValidation` / `QueryResult` | Ejecuta `EXPLAIN` y lectura read-only |
| Task Budget Policy | Uso y límites | Decisión gobernada | Impide superar intentos, replans, consultas, tokens y tiempo por tarea |
| Specialist Proposal Governance | Propuesta, seguridad, costo, revisión y caché | Estado de gate | Impide que un cache hit o una auto-revisión fallida llegue a HITL |
| Investigation Governance | Plan/decisión/uso | Plan o decisión validada | Controla autoridad y presupuestos acumulados antes de cada HITL |
| Agent Response Cache | Proyección versionada | Respuesta cacheada | Reduce llamadas sin cachear decisiones de autoridad |
| Excel Export | Evidencia persistida | XLSX | Exporta una consulta o investigación multi-evidencia |

Las tools determinísticas de 0.7.4 no se convierten artificialmente en agentes. SQLGlot, QueryEngine,
costo, memoria SQL, Excel, chart builder, presupuestos y coordinación siguen siendo código
controlado porque esa es la frontera adecuada de autoridad.

# Subgrafos especialistas y extensibilidad

Los especialistas se descubren desde `config/specialists.yaml`. El grafo padre no contiene un
`if/elif` por rol.

```yaml
specialists:
  collections:
    display_name: Agente de cobranzas
    description: Analiza morosidad, recuperación y promesas de pago.
    domains: [collections]
    capabilities: [delinquency, recovery, promise-to-pay]
    instructions: >-
      Usa únicamente métricas y fuentes certificadas del dominio collections.
    task_budget:
      max_attempts: 2
      max_replans: 1
      max_llm_tokens: 24000
      max_queries: 1
      max_active_seconds: 180
```

Para agregarlo:

1. Publicar `semantic_catalog/domains/collections/` y sus vistas `semantic.*`.
2. Agregar el perfil YAML.
3. Opcionalmente agregar un perfil específico en `config/agents.yaml`; si no existe, utiliza el
   preset default.
4. Reiniciar la API para recompilar la topología LangGraph.

No se modifica Python ni el grafo padre. Un perfil queda deshabilitado automáticamente cuando sus
dominios o contratos requeridos no están publicados.

# Paralelismo

El supervisor puede seleccionar varias tareas sin dependencias pendientes. LangGraph envía una
invocación aislada a cada subgrafo mediante `Send`.

- El fan-out está limitado por `AUTONOMOUS_MAX_PARALLEL_TASKS`.
- Las propuestas se preparan en paralelo.
- Las ejecuciones SQL permanecen secuenciales porque cada una requiere su propio HITL.
- Los reducers combinan propuestas y eventos sin compartir scratchpad entre especialistas.
- Las dependencias del plan impiden ejecutar una tarea antes de contar con su evidencia previa.

# Gobierno y presupuestos

## Presupuesto global

```dotenv
AUTONOMOUS_MAX_ITERATIONS=4
AUTONOMOUS_MAX_TASKS=8
AUTONOMOUS_MAX_PARALLEL_TASKS=3
AUTONOMOUS_MAX_QUERIES=4
AUTONOMOUS_MAX_LLM_TOKENS=120000
AUTONOMOUS_MAX_ACTIVE_EXECUTION_SECONDS=600
AUTONOMOUS_MAX_TOTAL_PLAN_COST=500000
AUTONOMOUS_MAX_TOTAL_PLAN_ROWS=1000000
AUTONOMOUS_MAX_TOTAL_RELATION_BYTES=2147483648
AUTONOMOUS_MAX_TOTAL_DATABASE_SECONDS=90
```

## Presupuesto por tarea

Cada perfil define límites que el LLM no puede aumentar:

- Intentos y replanificaciones.
- Tokens LLM.
- Consultas.
- Tiempo activo.
- Costo acumulado del plan.
- Filas acumuladas del plan.
- Bytes de relaciones examinadas.

`TaskBudgetPolicy` y `InvestigationGovernancePolicy` fallan de forma cerrada. El supervisor puede
terminar por presupuesto, pero no ampliarlo.

# Caché Redis para reducir llamadas LLM

`AgentResponseCache` usa claves SHA-256 y TTL. Se cachean únicamente trabajos repetibles:

- Resolución contextual con la misma proyección de memoria.
- Planes con la misma pregunta, catálogo, especialistas y presupuesto.
- Propuestas de especialistas con el mismo contrato de tarea, modelo y catálogo.

No se cachean:

- Credenciales o tokens de autenticación.
- Decisiones HITL.
- Filas de resultados SQL.
- Permisos ni validaciones de autoridad.
- Respuestas que dependan de evidencia cambiante sin incluirla en la huella.

```dotenv
AGENT_CACHE_ENABLED=true
AGENT_CACHE_NAMESPACE=axiz:agent-cache:v1
AGENT_CACHE_DEFAULT_TTL_SECONDS=900
```

Si Redis falla, el cache opera fail-open y la ejecución continúa sin omitir controles.

# Contexto, memoria y correcciones generalizadas

La clasificación contextual usa el contrato:

```text
independent_request
analytical_follow_up
session_reference
ambiguous
```

No se basa en excepciones por palabras de negocio. Un follow-up posterior a una ejecución se
convierte en una nueva revisión SQL, conserva los elementos no solicitados y vuelve a seguridad,
costo y HITL.

La memoria versión 3 conserva el contrato analítico principal y un resumen de la última
investigación multi-evidencia. No persiste chain-of-thought.

El feedback soporta límite, filtros, periodo, métricas, dimensiones, agrupación, orden, fuente y
regeneración semántica. SQLGlot aplica cambios mecánicos; el LLM interpreta cambios complejos; el
validador comprueba cumplimiento y modificaciones inesperadas.

# Evidencia, crítica y síntesis

Cada ejecución aprobada produce `InvestigationEvidence` con:

- `evidence_id`, tarea y especialista.
- Pregunta e interpretación.
- SQL y fuentes.
- Resultado acotado.
- Verificación.
- Resumen, hallazgos y caveats.
- Seguridad y costo asociados.

La síntesis usa `EvidenceBackedFinding`:

```json
{
  "statement": "La aprobación cayó en el canal X",
  "evidence_ids": ["evidence-a1", "evidence-b2"],
  "confidence": 0.91,
  "limitations": ["No se dispone de causalidad confirmada"]
}
```

El workflow rechaza una síntesis que cite IDs inexistentes.

# Modelo de datos

## Control plane: `axiz_agent_control`

| Tabla | Grain | Propósito |
|---|---|---|
| `app.users` | Usuario | Identidad y roles |
| `app.chat_sessions` | Conversación | Sesiones persistentes |
| `app.chat_messages` | Turno | Mensajes y metadata de UI/HITL |
| `app.agent_runs` | Run | Estado, lease, errores y snapshot |
| `app.session_memory` | Sesión | Memoria analítica estructurada |
| `app.human_feedback` | Decisión | Aprobación, rechazo o corrección |
| `app.audit_events` | Evento | Trazabilidad técnica y agentic |
| Checkpoints LangGraph | Thread/run | Reanudación durable de subgrafos e HITL |

## Data plane: `axiz_business_data`

```mermaid
flowchart LR
    O[operational: datos fuente sintéticos] --> A[analytics: dimensiones y hechos]
    A --> S[semantic: vistas gobernadas]
    S --> C[catálogo YAML]
    C --> AG[Subgrafos especialistas]
    S --> QE[agent_reader / QueryEngine]
```

El rol `agent_reader` solo tiene `SELECT` sobre `semantic`; no puede leer `operational`, `analytics`
ni el control plane.

La PoC funciona sin una base externa:

```dotenv
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

No se requiere ninguna base externa: el bootstrap crea datos sintéticos, capas analíticas y vistas
semánticas. En producción puede externalizarse únicamente el data plane:

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:password@db.example.com:5432/business_data?sslmode=verify-full
```

# Tecnologías

| Tecnología | Uso |
|---|---|
| Python 3.12 | Runtime |
| FastAPI | API, autenticación y SSE |
| LangGraph 1.x | Grafo padre, subgrafos, `Send`, reducers, HITL y checkpoints |
| Pydantic 2 | Contratos tipados y Structured Outputs |
| OpenAI Responses API | Proveedor cloud configurable |
| Ollama API | Proveedor local/privado opcional |
| SQLGlot | AST, normalización, feedback y seguridad |
| PostgreSQL 18 | Control plane, data plane y `EXPLAIN` |
| SQLAlchemy + psycopg 3 | Persistencia y ejecución |
| Redis | Caché agentic y estado temporal |
| Streamlit + Plotly | Chat, HITL, tablas y gráficos |
| XlsxWriter | Excel mono-consulta y multi-evidencia |
| Docker Compose | Entorno reproducible de la PoC |

Los nombres `gpt-5.6-*` incluidos en `config/agents.yaml` pueden representar aliases privados de un
gateway. El startup probe debe validarlos contra el proveedor real antes de usarlos.

# Excel multi-evidencia

Una investigación autónoma exporta:

- `Resumen`.
- Una hoja por evidencia aprobada.
- `Metadatos` con tarea, especialista, dominio, SQL y tiempos.

La exportación usa resultados persistidos; no vuelve a ejecutar SQL. Las fórmulas y URLs automáticas
están deshabilitadas para prevenir spreadsheet injection.

# Observabilidad y trayectoria

`AutonomousInvestigationSummary` publica:

- Plan y tareas.
- Propuestas de especialistas.
- Evidencias.
- Decisión del supervisor.
- Revisión crítica.
- Presupuestos y consumo.
- Trayectoria observable.

La trayectoria incluye acciones como:

```text
delegate
security_validated
cost_validated
proposal_created
proposal_selected_for_hitl
human_approved
sql_executed
evidence_recorded
evidence_reviewed
investigation_finalized
```

No se expone chain-of-thought; solo decisiones, gates, inputs/outputs contractuales y métricas.

# Evals agentic y pruebas end-to-end

La solución incorpora dos niveles.

## Evals offline reproducibles

`AgenticTrajectoryEvaluator` valida:

- Secuencia requerida de decisiones.
- Ausencia de acciones que exceden autoridad.
- Seguridad, costo y HITL antes de cada ejecución.
- Fan-out paralelo observable.
- Límites de tareas y olas.
- Hallazgos enlazados a evidencia existente.

Dataset:

```text
datasets/evals/autonomous_society.yaml
```

Ejecutar suite:

```bash
pytest -q
```

Evaluar un `RunResponse` persistido:

```bash
python scripts/run_agentic_evals.py run.json --case simple_governed_query
```

## E2E live con stack real

El runner inicia un run por API, aprueba cada HITL, espera la terminación y guarda el resultado:

```bash
python scripts/run_live_agentic_evals.py \
  --password "$BOOTSTRAP_PASSWORD" \
  --question "Investiga la variación de aprobación y sustenta la conclusión" \
  --output live-run.json
```

Después se aplica el evaluador offline. Este nivel consume modelos y datos configurados y debe
formar parte del pipeline de integración de un ambiente de prueba.

# Estructura principal

```text
src/axiz/pe/sql_agent/
├── agents/autonomous/          # planner, supervisor, especialistas y crítico
├── workflow/
│   ├── graph.py                # grafo padre
│   └── subgraphs/              # subgrafos especialista y crítico
├── services/
│   ├── specialist_registry.py
│   ├── specialist_graph_registry.py
│   ├── agent_cache.py
│   └── llm_usage.py
├── tools/
│   ├── investigation_governance.py
│   ├── proposal_governance.py
│   ├── task_budget.py
│   ├── sql_security.py
│   └── ...
└── evals/trajectory.py

config/specialists.yaml
datasets/evals/autonomous_society.yaml
scripts/run_agentic_evals.py
scripts/run_live_agentic_evals.py
```

# Inicio rápido

## Variables

```bash
cp .env.example .env
```

Configurar como mínimo:

```dotenv
OPENAI_API_KEY=<api-key-o-gateway-key>
APP_SECRET_KEY=<mínimo-32-caracteres>
BOOTSTRAP_PASSWORD=<contraseña-segura>
INTERNAL_SERVICE_KEY=<service-key-segura>
```

## Levantar

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

## Verificar

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
docker compose --env-file .env -f infrastructure/docker-compose.yml logs -f api streamlit
```

`/health/ready` informa especialistas habilitados, presupuesto autónomo, modelos, catálogo, Redis y
motor de datos.

# Endpoints principales

| Método | Ruta | Propósito |
|---|---|---|
| `POST` | `/api/v1/agent/runs` | Inicia investigación |
| `POST` | `/api/v1/agent/runs/stream` | Inicia por SSE |
| `POST` | `/api/v1/agent/runs/{runId}/feedback` | Aprueba, cambia o rechaza propuesta |
| `POST` | `/api/v1/agent/runs/{runId}/cancel` | Cancela el run |
| `GET` | `/api/v1/agent/runs/{runId}` | Recupera estado y trayectoria |
| `GET` | `/api/v1/agent/runs/{runId}/exports/excel` | Exporta todas las evidencias |
| `GET` | `/api/v1/catalog/specialists` | Lista perfiles y disponibilidad |
| `POST` | `/api/v1/catalog/reload` | Recarga catálogo; cambios de topología requieren restart |
| `GET` | `/health/ready` | Readiness completo |

Un HTTP `200` en SSE solo confirma que el stream se abrió. El resultado funcional está en los
eventos y en `RunResponse.status`.

# Validación del proyecto

```bash
pytest -q
python -m compileall -q src streamlit_app teams_adapter scripts tests
python - <<'PY'
import tomllib, yaml
from pathlib import Path

tomllib.loads(Path('pyproject.toml').read_text())
yaml.safe_load(Path('config/agents.yaml').read_text())
yaml.safe_load(Path('config/specialists.yaml').read_text())
yaml.safe_load(Path('datasets/evals/autonomous_society.yaml').read_text())
print('configuration ok')
PY
```

Las pruebas que requieren PostgreSQL, SQLGlot, Redis, LangGraph o proveedores reales se ejecutan en
la imagen Docker y en el runner live del ambiente de integración.

# Alcance y límites

Incluye una implementación profesional de referencia, pero sigue siendo una PoC. Antes de
producción se recomienda añadir:

- SSO corporativo y secret manager.
- TLS/mTLS y rotación de credenciales.
- Observabilidad centralizada y trazas OpenTelemetry/LangSmith.
- Golden datasets propios de Diners y evaluación humana.
- Pruebas de carga y recuperación de checkpoints con múltiples réplicas.
- Política de retención y clasificación de prompts/evidencia.
- Promoción versionada de prompts, catálogo y especialistas entre ambientes.
- Despliegue Kubernetes y pruebas de resiliencia.

Los agentes nunca deben obtener acceso DML/DDL ni credenciales superiores como parte de esta
evolución.
