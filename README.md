# Agente SQL

# Fuentes de verdad de una revisión

Usa representaciones complementarias:

| Representación | Responsabilidad |
|---|---|
| Mensaje original | Fuente humana de la modificación solicitada |
| SQL anterior completo | Baseline técnico aprobado que debe preservarse salvo cambios explícitos |
| SQL revisado completo | Fuente de verdad editable de la revisión |
| Diff AST | Evidencia estructural de qué cambió entre ambas sentencias |
| `SemanticQuerySpec` | Snapshot semántico derivado para memoria, auditoría y explicación |
| `CompiledSqlArtifact` | SQL validado, hash, referencia, controles y estado de ejecución |
| `EvidenceRecord` | Resultado creado únicamente después de una ejecución real |

Para feedback abierto no se obliga al usuario a encajar en un `QuerySpecPatch` con targets fijos.
El envelope interno conserva el mensaje completo, la referencia del baseline y la estrategia
`regenerate`; su lista de cambios tipados permanece vacía. El significado se resuelve dentro de
`SqlEngineerAgent` usando la sentencia completa y el catálogo.

Ejemplo de entrada a la revisión:

```json
{
  "raw_user_feedback": "quita amount_pen y muestra channel antes que city",
  "previous_sql": "SELECT transaction_id, amount_pen, city, channel ...",
  "revision_context": {
    "allowed_sources": ["semantic.v_payment_transactions"],
    "source_contracts": {
      "semantic.v_payment_transactions": {
        "columns": ["transaction_id", "amount_pen", "city", "channel"]
      }
    }
  }
}
```

Ejemplo de salida del agente:

```json
{
  "sql": "SELECT transaction_id, channel, city FROM semantic.v_payment_transactions ...",
  "change_summary": [
    "Se eliminó amount_pen",
    "channel se colocó antes que city"
  ],
  "requires_clarification": false
}
```

La aplicación deriva después un snapshot semántico desde el AST de la sentencia final. Por eso una
propiedad antigua o ausente en el JSON no puede reinsertar `amount_pen` ni bloquear el nuevo orden de
columnas.

# Estado compartido durante una revisión

El estado conserva la sentencia anterior, el mensaje, la propuesta final, el snapshot semántico, el
diff AST, las validaciones, el HITL y la evidencia. Guardar estos datos en PostgreSQL no consume
tokens. Solo consume tokens la proyección que se envía al LLM. En una revisión el agente recibe la
sentencia completa porque es necesaria para conservar filtros, joins, expresiones y columnas no
descritas en un contrato reducido; no recibe todo el historial ni todo el catálogo.

# Principios arquitectónicos

La solución separa dos clases de responsabilidad:

1. **Razonamiento y decisión:** agentes LLM con personalidad, contexto, contratos y limitaciones.
2. **Ejecución y control:** servicios determinísticos verificables.

Un componente se modela como agente solo cuando necesita interpretar, decidir, delegar, criticar o sintetizar. Validar SQL, analizar un AST, comprobar costos, ejecutar una consulta o persistir memoria no requiere personalidad ni autonomía y se implementa como servicio o skill.

# Arquitectura de la sociedad autónoma

```text
Usuario / HITL
      │
      ▼
InvestigationCoordinatorAgent
      │
      ├── ruta directa ────────────────┐
      │                                │
      └── investigación multi-tarea    │
              │                        │
              ▼                        ▼
       DomainAnalystAgent parametrizado por perfil
              │
              ▼
       SqlEngineerAgent
              │
              ▼
   Gobernanza determinística
   ├── catálogo semántico
   ├── SQLGlot AST
   ├── seguridad SQL
   ├── PostgreSQL EXPLAIN
   ├── presupuestos
   └── HITL
              │
              ▼
        QueryExecutor read-only
              │
              ▼
       EvidenceReviewerAgent
              │
              ├── finalizar
              ├── aclarar
              └── pedir nueva evidencia al coordinador
```

Esta sociedad autónoma permite:

- seleccionar una ruta directa o una investigación;
- descomponer objetivos en tareas;
- delegar por capacidades publicadas;
- ejecutar olas paralelas gobernadas;
- registrar evidencia;
- detectar evidencia faltante o contradictoria;
- replanificar;
- decidir cuándo se satisfacen los criterios de finalización.

>>La autonomía no incluye permisos para omitir seguridad, costo, presupuestos o HITL.

# Los cuatro agentes

El directorio de agentes contiene exclusivamente:

```text
src/axiz/pe/sql_agent/agents/
├── __init__.py
├── investigation_coordinator_agent.py
├── domain_analyst_agent.py
├── sql_engineer_agent.py
└── evidence_reviewer_agent.py
```

Los archivos bajo `skills/` no son identidades de agente. Son capacidades operativas que los cuatro agentes invocan en diferentes modos.

## InvestigationCoordinatorAgent

### Propósito

Coordinar el ciclo completo de investigación sin generar ni ejecutar SQL.

### Modos

- `context`: determina la relación del mensaje con la sesión.
- `route`: clasifica la intención y selecciona ruta o dominio.
- `plan`: crea tareas y criterios de finalización.
- `supervise`: delega, replantea o finaliza.
- `synthesize`: integra evidencia aceptada.
- `conversation`: responde preguntas sobre el estado de la conversación.

### Input lógico

```json
{
  "question": "Compara aprobación y liquidación por comercio",
  "memory_summary": {},
  "published_domains": [],
  "specialist_capabilities": [],
  "evidence_summary": [],
  "governed_budget": {}
}
```

### Output lógico

```json
{
  "mode": "plan",
  "route": "full_investigation",
  "tasks": [
    {
      "task_id": "task-1",
      "capability": "approval",
      "domain": "acquiring"
    },
    {
      "task_id": "task-2",
      "capability": "settlement",
      "domain": "acquiring"
    }
  ],
  "completion_criteria": [
    "Aprobación respaldada por evidencia",
    "Liquidación respaldada por evidencia"
  ]
}
```

### Limitaciones

- No genera ni ejecuta SQL.
- No cambia presupuestos.
- No modifica permisos.
- No omite seguridad, costo o HITL.
- No inventa dominios, capacidades o evidencia.

## DomainAnalystAgent

### Propósito

Refinar una tarea de negocio usando un perfil de dominio y el catálogo semántico publicado.

### Perfiles configurables

```text
acquiring
issuing
fraud
chargebacks
temporal
```

Todos usan la misma clase. El perfil determina personalidad especializada, capacidades, dominios permitidos, instrucciones y presupuesto.

### Input lógico

```json
{
  "task": {
    "task_id": "task-1",
    "objective": "Obtener transacciones reversadas",
    "capability": "merchant-performance"
  },
  "profile": {
    "role": "acquiring",
    "domains": ["acquiring"],
    "capabilities": ["approval", "settlement", "channel"]
  },
  "original_question": "Muéstrame las transacciones reversadas de los últimos 7 días",
  "memory_summary": {},
  "published_domains": [],
  "prior_evidence": []
}
```

### Output lógico

```json
{
  "task_id": "task-1",
  "specialist": "acquiring",
  "refined_question": "Detalle de transacciones REVERSED de los últimos 7 días completos",
  "domain": "acquiring",
  "expected_evidence": ["transaction detail"],
  "catalog_focus": ["payment_transaction"],
  "assumptions": [],
  "can_proceed": true
}
```

### Limitaciones

- Usa solo fuentes, métricas y dimensiones publicadas.
- No ejecuta SQL.
- No controla gates de seguridad o costo.
- No sustituye un dominio por otro.

## SqlEngineerAgent

### Propósito

Generar, revisar y reparar SQL, además de interpretar cualquier feedback de cambios mediante contexto semántico y Structured Output.

### Modos

- `generate`: crea SQL desde un contrato analítico.
- `interpret_feedback`: transforma lenguaje natural en intenciones tipadas.
- `revise`: regenera SQL cuando el cambio altera la semántica.
- `repair`: corrige SQL usando feedback de SQLGlot o PostgreSQL.
- `feedback_compliance`: comprueba que todos los cambios fueron aplicados.

### Input lógico

El agente recibe el mensaje completo, una referencia a la especificación vigente y el SQL anterior. La especificación completa permanece en el estado compartido y se adjunta solo cuando el modo la necesita:

```json
{
  "mode": "interpret_feedback",
  "question": "Muéstrame las transacciones reversadas de los últimos 7 días",
  "raw_user_message": "aumenta 7 días a la búsqueda",
  "query_spec_ref": {
    "id": "qs-payment-transactions",
    "version": 3
  },
  "semantic_query_spec": {
    "spec_id": "qs-payment-transactions",
    "version": 3,
    "filters": {
      "operator": "and",
      "expressions": [
        {
          "member": "transactions.status",
          "operator": "equals",
          "values": ["REVERSED"]
        }
      ]
    },
    "time_filters": [
      {
        "member": "transactions.transaction_date",
        "range": {"type": "relative", "unit": "day", "value": 7},
        "timezone": "America/Lima"
      }
    ],
    "limit": 500,
    "source_objects": ["semantic.v_payment_transactions"]
  },
  "previous_sql": "SELECT ... WHERE transaction_date >= current_date - 7 ..."
}
```

### Envelope de revisión

```json
{
  "feedback": "quiero que la búsqueda sea de los últimos 14 días",
  "raw_user_message": "quiero que la búsqueda sea de los últimos 14 días",
  "strategy": "regenerate",
  "changes": [],
  "requires_clarification": false,
  "query_spec_ref": {
    "id": "qs-payment-transactions",
    "version": 3
  }
}
```

El modo `revise` recibe directamente:

```json
{
  "raw_user_feedback": "quiero que la búsqueda sea de los últimos 14 días",
  "previous_sql": "SELECT ... WHERE transaction_date >= ... - 7 ...",
  "current_contract": {},
  "revision_context": {}
}
```

El resultado es una sentencia completa revisada y metadatos que describen esa sentencia final. La
misma ruta procesa cambios de filtros, proyección, posición de columnas, expresiones, aliases, joins,
agrupaciones, métricas, orden y límite. 

Después de la generación se calcula un diff AST genérico. Si el cambio solicitado no aparece en el
diff o la consulta introduce cambios no solicitados, `feedback_compliance` solicita una reparación.
Las ambigüedades reales se reportan mediante `requires_clarification` en `SqlGenerationOutput`.

### Limitaciones

- No ejecuta SQL.
- No selecciona fuentes no publicadas.
- No inventa columnas o valores categóricos.
- No usa regex ni diccionarios de frases para inferir intención.
- No omite validaciones.

## EvidenceReviewerAgent

### Propósito

Verificar que el resultado responde al objetivo más reciente aprobado, criticar evidencia acumulada y redactar una respuesta fundamentada.

### Modos

- `verify`
- `criticize`
- `explain`
- `catalog_answer`

### Input lógico

```json
{
  "mode": "verify",
  "question": "Muéstrame las transacciones reversadas de los últimos 14 días",
  "interpretation": "Transacciones REVERSED de los últimos 14 días completos",
  "raw_user_message": "quiero que la búsqueda sea de los últimos 14 días",
  "query_spec_ref": {"id": "qs-payment-transactions", "version": 4},
  "semantic_query_spec": {},
  "compiled_sql_artifact": {
    "execution_state": "executed",
    "sql_hash": "sha256:..."
  },
  "sql": "SELECT ...",
  "result": {
    "columns": ["transaction_id", "transaction_timestamp"],
    "rows": [],
    "row_count": 0
  },
  "completion_criteria": ["Periodo de 14 días", "Estado REVERSED"]
}
```

### Output lógico

```json
{
  "mode": "verify",
  "valid": true,
  "ready_to_finalize": true,
  "missing_evidence": [],
  "contradictions": [],
  "findings": [],
  "caveats": []
}
```

### Limitaciones

- No genera ni ejecuta SQL.
- No inventa evidencia.
- No puede aprobar seguridad, costo o HITL.
- Evalúa el objetivo revisado más reciente, no obliga a conservar la pregunta original cuando el usuario aprobó un cambio.

# Skills de los agentes

Las skills están organizadas por responsabilidad:

```text
src/axiz/pe/sql_agent/skills/
├── coordinator/
│   ├── context_resolution.py
│   ├── conversation_memory.py
│   ├── intent_routing.py
│   ├── complexity_routing.py
│   ├── investigation_planning.py
│   └── supervision.py
├── sql/
│   ├── generation.py
│   ├── feedback_planning.py
│   └── compliance.py
├── evidence/
│   ├── verification.py
│   ├── critique.py
│   └── explanation.py
├── domain_analysis.py
└── semantic_exploration.py
```

Una skill no tiene identidad autónoma, presupuesto independiente ni selección de modelo propia. 
Es ejecutada por uno de los cuatro agentes bajo su personalidad, contrato y límites.

# Configuración de personalidad y contratos

Archivo:

```text
config/agent_skills.yaml
```

Ejemplo resumido:

```yaml
agents:
  sql_engineer:
    personality: >-
      Act as a senior semantic SQL engineer.
    context: >-
      Revise the complete previous SQL using the complete user message and bounded semantic context.
    responsibilities:
      - Generate SQL from certified contracts.
      - Apply arbitrary feedback directly to the complete SQL statement.
      - Preserve unrequested clauses and reconcile dependent expressions.
    limitations:
      - Never execute SQL.
      - Never use regex or phrase dictionaries to infer user intent.
      - Never invent sources or columns.
    modes:
      interpret_feedback:
        input_contract: FeedbackInterpretationInvocation
        output_contract: SqlFeedbackPlan
```

`AgentSkillRegistry` carga esta configuración y la antepone al prompt específico de cada modo.

# Cómo se invocan los agentes

Los agentes no se invocan por nombre desde el chat. El usuario expresa un objetivo y el 
coordinador selecciona automáticamente la ruta y las capacidades.

## Consulta nueva

```http
POST /api/v1/agent/runs/stream
Content-Type: application/json
Authorization: Bearer <token>

{
  "session_id": "<uuid>",
  "question": "Muéstrame las transacciones reversadas de los últimos 7 días"
}
```

Flujo:

```text
Coordinator → Domain Analyst → SQL Engineer → Governance → HITL
```

## Revisión de SQL

La aprobación o solicitud de cambios reanuda el run interrumpido:

```json
{
  "decision": "request_changes",
  "comment": "aumenta 7 días a la búsqueda"
}
```

El `SqlEngineerAgent` recibe el comentario con el SQL y contrato anteriores. El usuario no debe indicar qué agente usar.

## Aprobación

```json
{
  "decision": "approve"
}
```

La ejecución permanece bloqueada hasta esta decisión.

## Contratos expuestos por API

```http
GET /api/v1/models/society-contracts
```

Devuelve los JSON Schema de input y output de los cuatro roles.

```http
GET /api/v1/models/agent-skills
```

Devuelve personalidad, contexto, responsabilidades, limitaciones y modos activos de cada agente.

```http
GET /api/v1/models/query-spec-contracts
```

Devuelve los JSON Schema de `SemanticQuerySpec`, `QuerySpecPatch`, `QuerySpecResolution` y `CompiledSqlArtifact`.

# Servicios determinísticos

Los siguientes componentes no son agentes:

- `SemanticCatalogTool`
- `SqlAstAnalyzer`
- `SqlSecurityValidator`
- `SqlFeedbackComplianceValidator`
- `QueryEngine`
- `InvestigationGovernancePolicy`
- `TaskBudgetPolicy`
- `RunExecutionCoordinator`
- `StructuredConversationMemoryService`
- `SemanticQuerySpecService`
- `AgentResponseCache`
- `AuditLogger`

## SQLGlot como autoridad estructural

El LLM interpreta el lenguaje natural, pero no modifica el SQL de manera libre cuando el cambio puede verificarse estructuralmente.

Ejemplo:

```text
Feedback completo: aumenta 7 días
SQL anterior completo: ... transaction_date >= fecha_actual - 7 ...
SqlEngineerAgent: devuelve una sentencia completa con - 14
SQLGlot: calcula el diff estructural entre ambas sentencias
Compliance: comprueba el mensaje original contra el SQL y el diff
Security validator: valida fuentes y columnas
EXPLAIN: valida costo
HITL: solicita aprobación
```

El código no necesita un target temporal ni conocer todas las maneras lingüísticas de decir
“aumentar”. SQLGlot verifica estructura; no interpreta el lenguaje natural.

# Rutas de ejecución

## Ruta directa

Para una sola evidencia:

```text
Coordinator → Domain Analyst → SQL Engineer → HITL → Execute → Reviewer
```

## Revisión

Para cambios sobre una consulta anterior:

```text
Coordinator context mode
→ SQL Engineer interpret_feedback
→ AST rewrite o semantic regeneration
→ seguridad y costo
→ HITL
```

## Investigación autónoma

Para objetivos multi-evidencia:

```text
Coordinator plan
→ varios Domain Analyst
→ varios SQL Engineer
→ Evidence Ledger
→ Evidence Reviewer
→ Coordinator replan/finalize
```

# Evidence Ledger

Cada evidencia conserva:

- identificador de investigación;
- tarea y perfil delegado;
- contrato analítico;
- SQL validado;
- resultado o referencia al resultado;
- verificación;
- presupuesto consumido;
- trazabilidad de decisiones.

Los agentes reciben proyecciones compactas del ledger y no toda la conversación completa.

# Catálogo semántico

El catálogo está bajo:

```text
semantic_catalog/domains/
```

Publica:

- fuentes permitidas;
- columnas;
- métricas;
- dimensiones;
- valores categóricos;
- calendarios;
- ejemplos certificados;
- políticas de consulta.

# Proveedores LLM

Los presets se configuran en:

```text
config/agents.yaml
```

Se soportan:

- OpenAI
- Anthropic
- Ollama

Solo existen cuatro asignaciones de modelo:

```dotenv
AXIZ_INVESTIGATION_COORDINATOR_MODEL_PRESET=openai_gpt_5_6_terra_balanced
AXIZ_DOMAIN_ANALYST_MODEL_PRESET=openai_gpt_5_6_luna_routing
AXIZ_SQL_ENGINEER_MODEL_PRESET=openai_gpt_5_6_terra_sql
AXIZ_EVIDENCE_REVIEWER_MODEL_PRESET=openai_gpt_5_6_luna_explanation
```

## Anthropic

```dotenv
ANTHROPIC_API_KEY=<secret>
AXIZ_INVESTIGATION_COORDINATOR_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_DOMAIN_ANALYST_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_SQL_ENGINEER_MODEL_PRESET=anthropic_claude_sonnet_5_sql
AXIZ_EVIDENCE_REVIEWER_MODEL_PRESET=anthropic_claude_sonnet_5_explanation
```

## Ollama

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
AXIZ_INVESTIGATION_COORDINATOR_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_DOMAIN_ANALYST_MODEL_PRESET=ollama_qwen3_8b_structured
AXIZ_SQL_ENGINEER_MODEL_PRESET=ollama_qwen3_coder_30b_sql
AXIZ_EVIDENCE_REVIEWER_MODEL_PRESET=ollama_gpt_oss_20b_reasoning
```

# Variables obligatorias

Crear `.env` desde el ejemplo:

```bash
cp .env.example .env
```

Configurar como mínimo:

```dotenv
OPENAI_API_KEY=<secret>
APP_SECRET_KEY=<mínimo-32-caracteres>
BOOTSTRAP_PASSWORD=<contraseña-segura>
INTERNAL_SERVICE_KEY=<service-key-segura>

AGENT_SKILLS_CONFIG_PATH=/app/config/agent_skills.yaml
AGENT_CACHE_NAMESPACE=axiz:agent-cache:v18
```

# Inicio con Docker

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up --build -d
```

## Reconstrucción limpia después de actualizar

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  down

docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  build --no-cache api streamlit

docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up -d
```

No es necesario borrar los volúmenes de PostgreSQL o Redis.

# Observabilidad

Variables recomendadas:

```dotenv
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_HTTP_REQUESTS=true
LOG_HEALTH_CHECKS=false
LOG_WORKFLOW_STAGES=true
LOG_LLM_CALLS=true
LOG_QUERY_EVENTS=true
LOG_SQL_TEXT=false
```

Los health checks permanecen activos, pero sus accesos no se registran cuando `LOG_HEALTH_CHECKS=false`.

# Casos de uso conversacionales

1. `Muéstrame las transacciones reversadas de los últimos 7 días.`
2. `Quiero que la búsqueda sea de los últimos 14 días.`
4. `Muéstrame las 10 últimas transacciones rechazadas con comercio y código de respuesta.`
5. `¿Cuál fue la tasa de aprobación de los últimos 7 días por canal?`
6. `¿Qué comercios tuvieron mayor facturación durante el último mes cerrado?`
7. `Compara la facturación del último mes cerrado con el mes anterior por marca.`
8. `Compara el último mes contra los dos meses anteriores acumulados.`
9. `Lista los comercios con más fallas de liquidación durante los últimos 30 días.`
10. `Compara POS y comercio electrónico durante los últimos 14 días.`
11. `¿Cuáles fueron los principales motivos de contracargo durante seis meses?`
12. `¿Qué fuentes y métricas están publicadas para adquirencia?`

# Resumen de contratos de agentes y tools

| Agente | Entrada | Salida | Descripción breve |
|---|---|---|---|
| InvestigationCoordinatorAgent | `CoordinatorInvocation`, contexto y ledger | `CoordinatorResult`, `InvestigationPlan`, `SupervisorDecision` | Resuelve contexto, enruta, planifica, supervisa y sintetiza |
| DomainAnalystAgent | `DomainAnalystInvocation` + perfil | `DomainAnalystResult` / `SpecialistTaskOutput` | Refina la tarea con capacidades y catálogo de dominio |
| SqlEngineerAgent | mensaje completo, SQL previo y catálogo acotado | `SqlGenerationOutput`, diff AST y `CompiledSqlArtifact` | Revisa la sentencia completa sin vocabulario cerrado de feedback |
| EvidenceReviewerAgent | mensaje original + query spec + artefacto + evidencia | `EvidenceReviewerResult`, `VerificationOutput` | Contrasta intención tipada, SQL ejecutado y evidencia |

El modo de routing del coordinador produce `IntentDomainOutput`. La generación y revisión del SQL producen `SqlGenerationOutput`.

| Tool | Entrada | Salida | Descripción breve |
|---|---|---|---|
| SemanticCatalogTool | dominio y términos | contratos semánticos | Publica fuentes, columnas, métricas y dimensiones permitidas |
| SemanticQuerySpecService | SQL final + metadatos semánticos | snapshot versionado derivado del AST | Mantiene memoria y auditoría sin gobernar feedback con campos fijos |
| CompiledSqlArtifact validator | query spec + SQL | validaciones y estado | Comprueba proyección, filtros, orden, límite y fuentes |
| SqlAstAnalyzer | SQL | estructura AST | Detecta fuentes, intervalos, ventanas, CTE, filtros y límites |
| SqlFeedbackApplier | SQL + plan tipado | `SqlFeedbackApplication` | Aplica postcondiciones estructurales sin interpretar lenguaje natural |
| SqlSecurityValidator | SQL + allowlist | `SecurityValidation` | Impide escritura, fuentes o columnas no permitidas |
| QueryEngine.estimate_cost | SQL validado | `CostValidation` | Ejecuta `EXPLAIN` y aplica límites de costo |
| QueryEngine.execute | SQL aprobado | `QueryResult` | Ejecuta en modo read-only después de HITL |
| TaskBudgetPolicy | uso + presupuesto | decisión de presupuesto | Controla tokens, intentos, consultas y tiempo |

# Modos de base de datos

## PoC integrada

```dotenv
BUSINESS_DATA_MODE=embedded
```

En este modo se levantan PostgreSQL, datos de ejemplo, vistas analíticas y catálogo semántico dentro del `docker-compose`. **No se requiere ninguna base externa**.

## Base empresarial externa

```dotenv
BUSINESS_DATA_MODE=external
AGENT_DATABASE_URL=postgresql://agent_reader:<password>@<host>:5432/<database>
```

En este modo el control plane sigue usando su propia base, mientras el agente consulta una fuente PostgreSQL externa mediante un usuario read-only. La fuente externa debe publicar contratos equivalentes en el catálogo semántico.

