# Axiz SQL Agent PoC 0.9.8

Sociedad autónoma gobernada de agentes para analítica Text-to-SQL. La solución transforma una
solicitud de negocio en evidencia SQL verificable, delega el trabajo a especialistas configurables,
solicita aprobación humana antes de cada ejecución y produce respuestas trazables a resultados
validados.

La autonomía tiene fronteras explícitas: los agentes pueden decidir **cómo investigar**, pero no
pueden cambiar permisos, ampliar presupuestos, omitir seguridad, saltarse `EXPLAIN`, ejecutar SQL
sin HITL ni alterar políticas financieras.

La versión 0.9.8 optimiza las revisiones estructurales de una consulta previamente aprobada. Un
feedback completo y no ambiguo como «ponle un límite de 100 registros a la query» se interpreta y
aplica de forma determinística sobre el AST, sin volver a invocar al intérprete LLM, al generador SQL
ni al auto-revisor del especialista. La consulta revisada conserva el contrato anterior y vuelve a
pasar seguridad, `EXPLAIN`/costo y HITL. Esto corrige el agotamiento observado de 26,282 frente al
presupuesto de 24,000 tokens sin ampliar arbitrariamente el límite. Se mantienen Anthropic, los
contratos semánticos por vista, el calendario `America/Lima`, la vista agregada de liquidaciones y el
logo corporativo de alta resolución incorporados en la versión anterior.

# Evolución de la solución

La rama `agente-workflow-orquestado` conserva `axiz-pe-sql-agent-poc-0.7.4`, anterior a la
transformación autónoma.

| Aspecto | `agente-workflow-orquestado` 0.7.4 | Sociedad autónoma 0.9.8 |
|---|---|---|
| Unidad principal | Workflow SQL central | Grafo padre + subgrafos especialistas |
| Delegación | Secuencia predeterminada | Supervisor y router semántico |
| Especialistas | Clases invocadas por nodos | Subgrafos aislados por invocación |
| Planificación | Flujo fijo | Adaptativa según complejidad |
| Paralelismo | No | Fan-out acotado mediante `Send` |
| Replanificación | Feedback humano | Supervisor y crítico pueden pedir evidencia |
| Presupuestos | Run y consulta | Globales, por tarea y reservas concurrentes |
| Evidencia | Resultado principal | Ledger multi-evidencia con `evidence_ids` |
| Caché | Técnica limitada | Redis versionado por etapa |
| Evaluación | Unitarias y contratos | Unitarias, trayectoria agentic y runner E2E live |

Se conservan las capacidades de 0.7.4: clasificación contextual, memoria estructurada, catálogo
semántico, feedback generalizado, SQLGlot, análisis de costo, HITL, lectura PostgreSQL, verificación,
explicación, gráficos, Excel, SSE, idempotencia, leases, cancelación, OpenAI, Anthropic, Ollama, Streamlit y
Teams opcional.

# Optimización adaptativa

La solución evita aplicar el ciclo autónomo completo cuando una sola evidencia SQL es suficiente.
También limita el contexto enviado a cada llamada y reserva la auto-revisión LLM para propuestas
con señales de riesgo. La versión 0.9.8 reduce además la proyección de `EXPLAIN`, reemplaza de
forma idempotente el costo del candidato SQL durante reparaciones y evita regeneraciones LLM para
cambios estructurales determinísticos sobre SQL previamente aprobado.

La arquitectura aplica cinco mecanismos generales.

## Router de complejidad semántica

Antes de planificar, `AutonomousComplexityRouterAgent` selecciona la ruta mínima suficiente:

```text
Solicitud analítica
        ↓
Router autónomo gobernado
        ├── direct_specialist
        │      └── una tarea + un especialista + una evidencia
        │
        └── full_investigation
               └── planner + fan-out + crítico + replanning + síntesis
```

`direct_specialist` sigue siendo autónomo: el router elige un especialista habilitado, construye una
tarea gobernada y la delega a su subgrafo. Se omiten únicamente llamadas que no agregan valor para
una tarea de evidencia única: planner, crítico y síntesis multi-evidencia.

`full_investigation` se utiliza cuando el objetivo requiere varias evidencias independientes,
múltiples especialistas, hipótesis, diagnóstico, conciliación de contradicciones o replanteamiento.

La decisión se obtiene mediante un contrato tipado:

```text
AutonomousRoutingDecision
├── mode
├── specialist
├── domain
├── task_objective
├── expected_evidence
├── query_mode
├── complexity_signals
├── confidence
└── clarification
```

El router no puede aprobar SQL, cambiar seguridad ni modificar presupuestos.

## Contexto semántico proyectado

`SemanticContextProjector` construye un contexto mínimo por tarea y etapa. La selección se basa en
relevancia semántica, score del catálogo y foco declarado por la tarea; no usa excepciones por
nombres de negocio.

El contexto conserva siempre:

- Allowlist de fuentes.
- Política de consulta.
- Métricas y dimensiones relevantes.
- Definición del dominio.
- Documentos y ejemplos limitados.
- Huella del catálogo y del contrato de proyección.

El generador recibe un contexto compacto. La revisión recibe una proyección todavía menor que no
repite documentos, ejemplos, historial ni memoria que no necesita. El árbol completo de `EXPLAIN`
no se envía al LLM: solo se proyectan sus métricas resumidas de costo, filas, relaciones y alertas.

## Revisión LLM condicionada por riesgo

`ProposalReviewPolicy` decide determinísticamente si una propuesta necesita una auto-revisión LLM
adicional. Seguridad, costo y HITL continúan siendo obligatorios en todos los casos.

La revisión LLM se activa por señales generales como:

- Varias fuentes.
- Supuestos semánticos.
- Regeneración semántica o híbrida.
- Costo o filas próximos al presupuesto.
- Joins, CTE, subconsultas, ventanas o `HAVING`.
- Contexto semántico sin versión verificable.

Una consulta simple, de una fuente y sin supuestos, puede superar la revisión mediante controles
determinísticos y evitar una llamada adicional.

## Revisiones estructurales sin regeneración LLM

Los cambios que no modifican el significado analítico se procesan mediante una ruta rápida y
controlada. Actualmente, una solicitud completa y no ambigua de `LIMIT` se reconoce localmente. Las
expresiones están ancladas al mensaje completo, por lo que una solicitud mixta como «cambia el
límite y agrega un filtro» no se degrada incorrectamente a un cambio estructural.

```text
Feedback: "ponle un límite de 100 registros a la query"
        ↓
Plan tipado local: set_limit(100), strategy=ast_only
        ↓
Reutilizar SQL y contrato previamente aprobados
        ↓
Aplicar LIMIT mediante SQLGlot
        ↓
SQLGlot seguridad → EXPLAIN/costo → HITL → ejecución read-only
```

En esta ruta se omiten únicamente tres llamadas redundantes:

- Intérprete LLM de feedback.
- Regeneración completa de SQL.
- Auto-revisión LLM del especialista.

El presupuesto por tarea permanece en `24,000` tokens. No se incrementa para ocultar un flujo
ineficiente. Los cambios semánticos, ambiguos o combinados continúan usando el agente y sus límites
normales.

## Reparación automática de SQL rechazado por PostgreSQL

La validación de costo ejecuta `EXPLAIN (FORMAT JSON)` antes del HITL. Si PostgreSQL detecta un
problema determinístico —sintaxis, columna inexistente, función inválida o incompatibilidad de
tipos— el motor ya no propaga la excepción como fallo general de la API. La convierte en una
`CostValidation` rechazada y devuelve al especialista:

- Código SQLSTATE y mensaje primario sanitizado.
- SQL fallido de la iteración anterior.
- Instrucción explícita para usar únicamente columnas, fuentes y valores del catálogo.
- Un nuevo intento sujeto al mismo presupuesto de tarea.

Para el caso «últimas transacciones», el catálogo publica `transaction_timestamp` y define que los
únicos estados válidos son `APPROVED`, `DECLINED` y `REVERSED`. Expresiones como «transacciones
ejecutadas» se interpretan como registros realizados y no generan el estado inexistente `EXECUTED`.

## Caché multinivel

Redis puede evitar llamadas repetidas en:

- Resolución contextual.
- Intención y dominio.
- Routing de complejidad.
- Recuperación y proyección semántica.
- Plan de investigación.
- Propuesta de especialista.

Las claves incluyen pregunta normalizada, contrato, modelo efectivo, versión del catálogo y estado
analítico relevante.

```dotenv
AGENT_CACHE_ENABLED=true
AGENT_CACHE_NAMESPACE=axiz:agent-cache:v7
AGENT_CACHE_DEFAULT_TTL_SECONDS=900
```

Un cache hit nunca evita:

```text
SQLGlot → EXPLAIN/costo → presupuesto → HITL → ejecución read-only
```

No se cachean credenciales, permisos, decisiones HITL ni resultados SQL completos. Si Redis falla,
la solución continúa sin caché, pero nunca falla de forma abierta respecto de seguridad.

# Decisiones de autonomía y gobierno

## Decisiones autónomas

- Descomponer objetivos complejos.
- Seleccionar especialistas habilitados.
- Crear y priorizar tareas.
- Preparar propuestas en paralelo.
- Formular hipótesis y evidencia esperada.
- Solicitar evidencia adicional.
- Rechazar conclusiones insuficientes.
- Replanificar dentro de límites.
- Sintetizar hallazgos vinculados a evidencia.

## Controles determinísticos obligatorios

- Identidad, credenciales y roles PostgreSQL.
- Allowlist de fuentes y esquemas prohibidos.
- Parsing y políticas SQLGlot.
- Límites globales y por tarea.
- Análisis PostgreSQL `EXPLAIN`.
- HITL antes de cada consulta.
- Ejecución mediante `QueryEngine` read-only.
- Idempotencia, leases, heartbeat y cancelación.
- Auditoría y protección de exportación Excel.

No se convierten en agentes las capacidades donde el razonamiento probabilístico aumentaría riesgo
regulatorio, financiero o de seguridad.

# Arquitectura

```mermaid
flowchart TD
    U[Usuario] --> UI[Streamlit / Teams]
    UI --> API[FastAPI + SSE]
    API --> CR[Context Resolver]
    CR --> ID[Intent & Domain]
    ID --> AR[Adaptive Autonomous Router]

    AR -->|direct_specialist| DS[Delegación directa]
    AR -->|full_investigation| PL[Planner]
    PL --> SUP[Supervisor]
    SUP --> DISP[Dispatcher gobernado]

    DS --> SEND[LangGraph Send]
    DISP -->|ola paralela| SEND
    SEND --> S1[Subgrafo especialista A]
    SEND --> S2[Subgrafo especialista B]
    SEND --> SN[Subgrafo especialista N]

    subgraph SG[Subgrafo especialista]
        T[Preparar o hidratar tarea] --> C[Contexto semántico compacto]
        C --> G[Generar o revisar SQL]
        G --> SEC[SQLGlot]
        SEC --> COST[EXPLAIN + presupuesto]
        COST --> RP[Review Policy]
        RP -->|riesgo bajo| DR[Revisión determinística]
        RP -->|riesgo alto| LR[Revisión LLM]
        DR --> PROP[Propuesta]
        LR --> PROP
    end

    S1 -.-> SG
    S2 -.-> SG
    SN -.-> SG

    PROP --> Q[Cola de propuestas]
    Q --> HITL[HITL obligatorio]
    HITL --> EXEC[QueryEngine read-only]
    EXEC --> VER[Result Verifier]
    VER --> LEDGER[Ledger de evidencia]

    LEDGER -->|modo directo| DSYN[Síntesis directa grounded]
    LEDGER -->|investigación completa| CRIT[Subgrafo crítico]
    CRIT --> SUP
    SUP -->|finalizar| SYN[Síntesis multi-evidencia]

    CR --> CACHE[(Redis)]
    ID --> CACHE
    AR --> CACHE
    C --> CACHE
    PL --> CACHE
    PROP --> CACHE

    API --> CTRL[(axiz_agent_control)]
    EXEC --> DATA[(axiz_business_data / semantic)]
```


## Interfaz e identidad visual

Streamlit utiliza el logotipo corporativo Axiz empaquetado en `streamlit_app/assets/` como base
del favicon del navegador y como marca visible en el acceso y la barra lateral. La interfaz aplica
la paleta institucional —azul marino, borgoña, blanco y gris— mediante `.streamlit/config.toml` y
estilos locales. No depende de URLs ni recursos remotos, por lo que el branding se conserva en
ejecución local y dentro del contenedor.

# Uso de LangGraph

LangGraph se utiliza como runtime de la sociedad autónoma:

1. El **grafo padre** conserva estado durable, presupuestos, tareas, olas, propuestas, HITL,
   evidencias, crítico y síntesis.
2. Cada especialista es un **subgrafo aislado por invocación**.
3. `Send` permite fan-out paralelo de tareas independientes.
4. Los reducers consolidan propuestas y eventos producidos en paralelo.
5. `interrupt()` detiene el flujo para revisión humana y el checkpointer permite reanudarlo.

El paso de workflow a autonomía no consistió en hacer probabilístico todo el sistema. Se añadieron
planificación, delegación, crítica y replanteamiento, manteniendo determinísticos los gates que
materializan riesgo.

# Flujo end-to-end

## Ruta directa adaptativa

```text
Contexto
→ intención/dominio
→ router
→ especialista
→ contexto compacto
→ SQL
→ seguridad
→ costo
→ revisión condicional
→ HITL
→ ejecución
→ verificación
→ evidencia
→ respuesta grounded
```

## Investigación completa

```text
Contexto
→ intención/dominio
→ router
→ planner
→ supervisor
→ fan-out paralelo
→ propuestas
→ HITL por consulta
→ ejecución y evidencia
→ crítico
→ replanning o finalización
→ síntesis multi-evidencia
```

Una modificación posterior a una ejecución se representa como `revise_previous`, conserva los
elementos no solicitados y vuelve a pasar por el mismo pipeline de gobierno.

# Especialistas extensibles

Los especialistas se registran en:

```text
config/specialists.yaml
```

Cada perfil define:

- Rol y nombre visible.
- Dominios compatibles.
- Capacidades.
- Perfil de modelo.
- Nombre del nodo LangGraph.
- Presupuesto por tarea.
- TTL de caché.

Para agregar otro especialista:

1. Publicar su dominio en `semantic_catalog/domains/<dominio>/`.
2. Publicar vistas autorizadas en el esquema `semantic`.
3. Añadir el perfil en `config/specialists.yaml`.
4. Añadir el perfil de modelo en `config/agents.yaml` cuando corresponda.
5. Reiniciar la API para recompilar la topología de subgrafos.

El grafo padre no contiene ramas por dominio.

# Agentes

| Agente | Entrada | Salida | Descripción breve |
|---|---|---|---|
| Context Resolver | Mensaje, memoria e historial acotado | `ContextResolutionOutput` | Determina la relación del mensaje con la sesión |
| Intent & Domain | Pregunta y dominios publicados | `IntentDomainOutput` | Clasifica intención y dominio |
| Autonomous Complexity Router | Pregunta, memoria, especialistas y presupuesto | `AutonomousRoutingDecision` | Elige ruta directa o investigación completa |
| Investigation Planner | Objetivo, especialistas y presupuesto | `InvestigationPlan` | Descompone investigaciones complejas |
| Autonomous Supervisor | Plan, evidencia, crítico y consumo | `SupervisorDecision` | Delega, solicita evidencia, rechaza o finaliza |
| Domain Specialist | Tarea, contexto y evidencia previa | `SpecialistTaskOutput` | Refina una tarea dentro de un subgrafo aislado |
| Semantic Explorer | Pregunta refinada y dominio | Contexto semántico proyectado | Recupera contratos semánticos gobernados |
| SQL Generator | Pregunta, catálogo, memoria y feedback | `SqlGenerationOutput` | Genera o repara SQL de solo lectura |
| Feedback Interpreter | Comentario, SQL anterior y contrato | `SqlFeedbackPlan` | Convierte correcciones libres en cambios tipados |
| Feedback Compliance | Plan y SQL anterior/revisado | `FeedbackComplianceResult` | Comprueba cambios e invariantes |
| Result Verifier | Pregunta, SQL y `QueryResult` | `VerificationOutput` | Valida que el resultado responda la solicitud |
| Critic | Plan y ledger de evidencia | `CriticReviewOutput` | Detecta contradicciones y evidencia faltante |
| Explanation / Synthesis | Evidencia verificada | Respuesta y `EvidenceBackedFinding` | Produce respuestas vinculadas a evidencia |

# Tools y servicios determinísticos

| Tool | Entrada | Salida | Descripción breve |
|---|---|---|---|
| Semantic Catalog Tool | Dominio y búsqueda | Contratos, símbolos y allowlist | Fuente de verdad semántica YAML |
| Semantic Context Projector | Pregunta, foco y contexto completo | Contexto compacto versionado | Reduce contexto sin alterar permisos ni políticas |
| Proposal Review Policy | SQL, contrato, costo y contexto | `ProposalReviewDecision` | Decide si se justifica una revisión LLM |
| SQL Feedback Applier | SQL y `SqlFeedbackPlan` | SQL transformado | Aplica cambios AST seguros |
| SQL Feedback Compliance Validator | Plan y SQL anterior/final | `FeedbackComplianceResult` | Verifica cumplimiento e invariantes |
| SQL Security Validator | SQL, allowlist y política | `SecurityValidation` | Bloquea operaciones y fuentes no permitidas |
| Query Engine | SQL validado | `CostValidation` / `QueryResult` | Analiza costo y ejecuta lectura neutral |
| Investigation Governance | Plan, decisión y consumo | Plan o decisión validada | Controla autoridad y presupuesto acumulado |
| Task Budget Policy | Uso y límites | Decisión de presupuesto | Limita intentos, replans, tokens, SQL y tiempo |
| Agent Response Cache | Clave versionada y contrato | Respuesta cacheada | Reduce llamadas mediante Redis |
| Chart Builder | `QueryResult` | `VisualizationSpec` | Selecciona visualización determinística |
| Excel Export | Evidencia persistida | XLSX | Exporta sin reejecutar SQL |

# Presupuestos

## Globales

```dotenv
AUTONOMOUS_MAX_ITERATIONS=4
AUTONOMOUS_MAX_TASKS=8
AUTONOMOUS_MAX_PARALLEL_TASKS=3
AUTONOMOUS_MAX_QUERIES=4
AUTONOMOUS_MAX_LLM_TOKENS=120000
AUTONOMOUS_MAX_ACTIVE_EXECUTION_SECONDS=600
```

También existen límites acumulados de costo, filas del plan, bytes de relaciones y tiempo de base.
Las reservas de tokens son concurrentes y atómicas para impedir sobreasignación durante fan-out.

## Por tarea

Cada `TaskBudget` puede limitar:

- Intentos.
- Replanificaciones.
- Consultas.
- Tokens.
- Costo.
- Filas.
- Bytes.
- Tiempo activo y tiempo SQL.

El presupuesto no puede ampliarse desde prompts o decisiones del supervisor.

# Clasificación contextual y memoria

La relación contextual usa:

```text
independent_request
analytical_follow_up
session_reference
ambiguous
```

La clasificación es semántica y no depende de listas de palabras de negocio.

`ConversationMemory` conserva la última solicitud analítica, dominio, métricas, dimensiones, filtros,
periodo, orden, límite, fuentes, SQL y resumen de investigación. No persiste chain-of-thought.

# Evidencia, crítica y grounding

Cada ejecución aprobada produce `InvestigationEvidence` con:

- Tarea y especialista.
- Pregunta e interpretación.
- SQL y fuentes.
- Resultado acotado.
- Verificación.
- Resumen, hallazgos y advertencias.
- Validación de seguridad y costo.

Los hallazgos finales usan:

```json
{
  "statement": "Hallazgo sustentado",
  "evidence_ids": ["evidence-123"],
  "confidence": 0.93,
  "limitations": []
}
```

El workflow rechaza referencias a evidencias inexistentes o no ejecutadas.

# Modelo de datos

## Control plane: `axiz_agent_control`

| Tabla | Grain | Propósito |
|---|---|---|
| `app.users` | Usuario | Identidad y roles |
| `app.chat_sessions` | Conversación | Sesiones persistentes |
| `app.chat_messages` | Turno | Mensajes y metadata de UI/HITL |
| `app.agent_runs` | Run | Estado, lease, error y snapshot |
| `app.session_memory` | Sesión | Memoria analítica estructurada |
| `app.human_feedback` | Decisión | Aprobación, rechazo o corrección |
| `app.audit_events` | Evento | Auditoría técnica y agentic |
| Checkpoints LangGraph | Thread/run | Interrupción y reanudación durable |

## Data plane: `axiz_business_data`

```mermaid
flowchart LR
    O[operational] --> A[analytics]
    A --> S[semantic]
    S --> QE[agent_reader / QueryEngine]
    C[Catálogo YAML] --> AG[Agentes y subgrafos]
    C -. describe .-> S
```

`agent_reader` solo tiene `SELECT` sobre `semantic` y no puede acceder al control plane ni a los
esquemas internos.

No se requiere ninguna base externa para ejecutar la PoC. Incluye un data plane embebido:

```dotenv
BUSINESS_DATA_MODE=embedded
AGENT_DATABASE_URL=postgresql://agent_reader:agent_readonly@postgres:5432/axiz_business_data
```

En producción se puede externalizar solamente la base de negocio sin modificar el workflow:

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:password@db.example.com:5432/business_data?sslmode=verify-full
```

# Tecnologías

| Tecnología | Uso |
|---|---|
| Python 3.12 | Runtime |
| FastAPI | API, autenticación y SSE |
| LangGraph 1.x | Grafo padre, subgrafos, `Send`, HITL y checkpoints |
| Pydantic 2 | Contratos y Structured Outputs |
| OpenAI Responses API | Proveedor cloud configurable |
| Anthropic Messages API | Proveedor Claude configurable con JSON Schema |
| Ollama API | Proveedor local o privado |
| SQLGlot | AST, normalización y seguridad |
| PostgreSQL 18 | Control plane, data plane y `EXPLAIN` |
| SQLAlchemy + psycopg 3 | Persistencia y ejecución |
| Redis | Caché agentic y estado temporal |
| Streamlit + Plotly | Chat, HITL, tablas y gráficos |
| XlsxWriter | Excel seguro y multi-evidencia |
| Docker Compose | Entorno reproducible |

# Observabilidad

`AutonomousInvestigationSummary` expone:

- Modo adaptativo y decisión de routing.
- Plan y tareas.
- Propuestas y tipo de revisión.
- Evidencias y hallazgos.
- Crítica y decisión del supervisor.
- Presupuesto y consumo.
- Trayectoria observable.

No se expone razonamiento privado. Se registran decisiones contractuales, gates, latencia, tokens,
cache hits y resultados de políticas.

# Evals agentic

`AgenticTrajectoryEvaluator` valida:

- Modo adaptativo esperado.
- Orden de decisiones requerido.
- Ausencia de acciones fuera de autoridad.
- Seguridad, costo y HITL antes de SQL.
- Fan-out paralelo observable.
- Límites por tarea.
- Uso condicionado de revisión LLM.
- Hallazgos enlazados a evidencia.
- Revalidación de propuestas obtenidas de caché.

Dataset:

```text
datasets/evals/autonomous_society.yaml
```

Ejecutar:

```bash
pytest -q
python scripts/run_agentic_evals.py run.json --case simple_governed_query
```

El runner live consume una API desplegada y aprueba cada HITL:

```bash
python scripts/run_live_agentic_evals.py \
  --password "$BOOTSTRAP_PASSWORD" \
  --question "Investiga un comportamiento y sustenta la conclusión" \
  --output live-run.json
```

# Exportación Excel

Una consulta directa exporta resultados y metadatos. Una investigación completa puede generar:

- `Resumen`.
- Una hoja por evidencia.
- `Metadatos` con tarea, especialista, SQL, dominio y tiempos.

La exportación usa resultados persistidos y nunca reejecuta SQL.

# Estructura principal

```text
src/axiz/pe/sql_agent/
├── agents/autonomous/
│   ├── complexity_router_agent.py
│   ├── investigation_planner_agent.py
│   ├── supervisor_agent.py
│   ├── domain_specialist_agent.py
│   └── critic_agent.py
├── workflow/
│   ├── graph.py
│   └── subgraphs/
├── services/
│   ├── specialist_registry.py
│   ├── specialist_graph_registry.py
│   ├── agent_cache.py
│   └── llm_usage.py
├── tools/
│   ├── semantic_context_projection.py
│   ├── proposal_review_policy.py
│   ├── investigation_governance.py
│   ├── task_budget.py
│   └── ...
└── evals/trajectory.py

streamlit_app/
├── app.py
├── api_client.py
└── assets/
    ├── axiz-agent-icon.svg
    ├── axiz-agent-icon.png
    ├── axiz-agent-icon@2x.png
    ├── axiz-logo.svg
    ├── axiz-logo.png
    ├── axiz-logo@2x.png
    ├── favicon.ico
    └── favicon.png
```

# Ejemplos de consultas para el agente

Estas consultas están alineadas con el dominio de adquirencia incluido en la PoC. Pueden copiarse
directamente en el chat:

1. `Dame las 20 últimas transacciones ejecutadas.`
2. `Muéstrame las 10 últimas transacciones rechazadas con comercio, monto y código de respuesta.`
3. `¿Cuál fue la tasa de aprobación de los últimos 7 días por canal?`
4. `¿Qué comercios tuvieron mayor facturación durante el último mes cerrado?`
5. `Compara la facturación del último mes cerrado con el mes anterior por marca de tarjeta.`
6. `¿Cómo evolucionó el monto procesado por MCC durante los últimos 30 días?`
7. `¿Cuántas transacciones fueron rechazadas ayer por código de respuesta?`
8. `Muéstrame la tasa de aprobación por ciudad durante el último mes cerrado.`
9. `¿Cuál es el ticket promedio por canal en el mes actual?`
10. `Lista los comercios con más fallas de liquidación durante los últimos 30 días.`
11. `Compara las transacciones internacionales por marca durante el último mes cerrado.`
12. `¿Cuánto ingreso por comisiones generó cada comercio durante el último mes cerrado?`
13. `Compara el volumen y monto procesado entre POS y ECOMMERCE durante los últimos 14 días.`
14. `Muéstrame las transacciones reversadas de los últimos 7 días, ordenadas de la más reciente a la más antigua.`
15. `¿Cuáles fueron los principales motivos de contracargo durante los últimos seis meses?`
16. `¿Qué significa la tasa de aprobación y cómo se calcula?`
17. `Explica qué fuentes y dimensiones puedo consultar en el dominio de adquirencia.`
18. `Sobre la consulta anterior, cambia el límite a 50 y conserva todos los filtros.`

Las preguntas que solicitan «últimas» o «más recientes» usan `transaction_timestamp`. El agente no
debe inventar `execution_timestamp` ni filtrar por `status = 'EXECUTED'`, porque esos valores no
existen en el contrato semántico.


## Ajuste de presupuestos gobernados por tarea

Las consultas simples pueden requerir hasta **tres intentos gobernados** cuando el primer `EXPLAIN`
encuentra una columna inválida o cuando la revisión solicita una corrección menor. Cada tarea sigue
reservando **una sola consulta ejecutable**; los intentos de generación y validación se contabilizan
con `max_attempts`, no como nuevas consultas de negocio:

```yaml
task_budget:
  max_attempts: 3
  max_replans: 1
  max_llm_tokens: 24000
  max_queries: 1
```

La reserva del slot de consulta es idempotente: repetir `EXPLAIN` o reparar el mismo SQL no aumenta
`queries`. Esto evita bloqueos prematuros como `Task budget exhausted: max_queries` sin ampliar el
permiso de ejecución ni permitir reintentos ilimitados. El presupuesto global autónomo continúa
limitando el total de consultas por investigación.

# Configuración de optimización

```dotenv
AUTONOMOUS_ADAPTIVE_ROUTING_ENABLED=true
AUTONOMOUS_CONDITIONAL_REVIEW_ENABLED=true
AUTONOMOUS_REVIEW_HIGH_COST_RATIO=0.70
AUTONOMOUS_REVIEW_HIGH_ROW_RATIO=0.70

SEMANTIC_CONTEXT_MAX_DOCUMENTS=4
SEMANTIC_CONTEXT_MAX_EXAMPLES=1
SEMANTIC_CONTEXT_MAX_METRICS=10
SEMANTIC_CONTEXT_MAX_DIMENSIONS=12
SEMANTIC_CONTEXT_MAX_DOCUMENT_ITEMS=8

SPECIALIST_HISTORY_MAX_MESSAGES=2
SPECIALIST_HISTORY_MAX_CHARS=1600
SPECIALIST_PRIOR_EVIDENCE_MAX_ITEMS=3
SPECIALIST_PRIOR_EVIDENCE_MAX_ROWS=2
```

Desactivar el routing adaptativo fuerza `full_investigation`. Desactivar la revisión condicional
fuerza revisión LLM para todas las propuestas, sin omitir los demás controles. Para controlar el
consumo, las propuestas simples de una fuente y símbolos publicados pasan revisión determinística;
los agentes de routing y especialistas usan salidas acotadas, y el SQL usa razonamiento medio.

# Proveedores y modelos

Los agentes no dependen de un proveedor concreto. Cada entrada de `config/agents.yaml` referencia un
preset completo con modelo, contexto, salida, timeout, reintentos y parámetros válidos para ese
proveedor. Se incluyen OpenAI, Anthropic y Ollama.

## Anthropic Claude

Configura la credencial en el `.env` de la raíz:

```dotenv
ANTHROPIC_API_KEY=<api-key>
ANTHROPIC_BASE_URL=https://api.anthropic.com
LLM_PROVIDER=anthropic
```

Presets incluidos:

| Preset | Uso recomendado |
|---|---|
| `anthropic_claude_opus_5_quality` | Investigación de máxima calidad |
| `anthropic_claude_sonnet_5_balanced` | Supervisión, planificación y verificación |
| `anthropic_claude_sonnet_5_sql` | Generación y reparación SQL |
| `anthropic_claude_sonnet_5_explanation` | Explicaciones y síntesis |
| `anthropic_claude_haiku_4_5_routing` | Clasificación y routing de bajo costo |

Ejemplo de routing completamente Anthropic:

```dotenv
AXIZ_DEFAULT_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_CONTEXT_RESOLVER_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_INTENT_DOMAIN_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_SQL_GENERATOR_MODEL_PRESET=anthropic_claude_sonnet_5_sql
AXIZ_RESULT_VERIFIER_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_EXPLANATION_MODEL_PRESET=anthropic_claude_sonnet_5_explanation
AXIZ_AUTONOMOUS_ROUTER_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_AUTONOMOUS_SUPERVISOR_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_INVESTIGATION_PLANNER_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_ACQUIRING_SPECIALIST_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_CRITIC_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_AUTONOMOUS_SYNTHESIS_MODEL_PRESET=anthropic_claude_sonnet_5_explanation
```

La integración usa `AsyncAnthropic`, envía el `system` en el campo superior de Messages y publica el
contrato Pydantic como `output_config.format` de tipo `json_schema`. Para Claude 4.7+ y Claude 5, los
presets dejan `temperature`, `top_p` y `top_k` en `null`; el adaptador no los envía. El razonamiento
adaptativo y el nivel de esfuerzo se configuran únicamente en los presets compatibles.

## Correcciones de las consultas observadas

### Fallas de liquidación por comercio

La pregunta `Lista los comercios con más fallas de liquidación durante los últimos 30 días` usa ahora
`semantic.v_merchant_settlement_metrics`. Esta vista tiene grano diario por comercio y métricas
certificadas como `failed_settlement_count`, por lo que evita escanear transacciones individuales.

### Rechazos de ayer por código

La pregunta `¿Cuántas transacciones fueron rechazadas ayer por código de respuesta?` usa únicamente
las columnas de `semantic.v_decline_analysis`, suma `declined_count` y aplica un intervalo semiabierto
con límites calculados en `America/Lima`:

```sql
WHERE metric_date >= (TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date - 1
  AND metric_date <  (TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date
```

El validador determinístico recibe contratos por fuente. Una columna publicada por otra vista ya no
se acepta solo por aparecer en el catálogo global. Además, los reintentos de una misma propuesta SQL
reemplazan su costo, filas y bytes estimados previos: no se acumulan varios `EXPLAIN` sobre el mismo
slot de consulta.

# Inicio rápido

## Preparar variables

```bash
cp .env.example .env
```

Configurar las credenciales de seguridad y al menos un proveedor:

```dotenv
OPENAI_API_KEY=<opcional>
ANTHROPIC_API_KEY=<opcional>
OLLAMA_BASE_URL=http://host.docker.internal:11434
APP_SECRET_KEY=<mínimo-32-caracteres>
BOOTSTRAP_PASSWORD=<contraseña-segura>
INTERNAL_SERVICE_KEY=<service-key-segura>
```

Para aplicar la nueva vista semántica en una base existente, reconstruye las imágenes y deja que el
bootstrap migre de `0.4.3` a `0.4.4`:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
```

## Levantar

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up --build -d
```

## Verificar

```bash
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
docker compose --env-file .env -f infrastructure/docker-compose.yml logs -f api streamlit
```

Accesos:

- Streamlit: `http://localhost:8501`
- FastAPI: `http://localhost:8000`
- OpenAPI: `http://localhost:8000/docs`

# Endpoints principales

| Método | Ruta | Propósito |
|---|---|---|
| `POST` | `/api/v1/agent/runs` | Inicia una solicitud |
| `POST` | `/api/v1/agent/runs/stream` | Inicia por SSE |
| `POST` | `/api/v1/agent/runs/{runId}/feedback` | Aprueba, cambia o rechaza |
| `POST` | `/api/v1/agent/runs/{runId}/cancel` | Cancela el run |
| `GET` | `/api/v1/agent/runs/{runId}` | Recupera estado y trayectoria |
| `GET` | `/api/v1/agent/runs/{runId}/exports/excel` | Exporta evidencia |
| `GET` | `/api/v1/catalog/specialists` | Lista especialistas efectivos |
| `GET` | `/health/ready` | Readiness completo |

Un HTTP `200` del endpoint SSE indica que el stream se abrió; el estado funcional está en los eventos
y en `RunResponse.status`.

# Validación

```bash
pytest -q
python -m compileall -q src streamlit_app teams_adapter scripts tests
python scripts/run_agentic_evals.py <run.json> --case <case_id>
```

Para aceptación previa a promoción debe ejecutarse además el runner E2E live contra PostgreSQL,
Redis, LangGraph y el proveedor de modelos configurado.

# Alcance

Esta es una PoC profesional de referencia. Para producción deben añadirse SSO corporativo, secret
manager, TLS/mTLS, observabilidad centralizada, datasets golden del negocio, evaluación humana,
pruebas de carga, promoción versionada de prompts/catálogo y despliegue Kubernetes resiliente.

Los agentes no deben recibir acceso DML/DDL ni credenciales superiores durante esa evolución.
