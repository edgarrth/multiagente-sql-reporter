# Validación técnica — Axiz SQL Agent PoC 0.9.12

# Alcance validado

- Routing autónomo adaptativo `direct_specialist` / `full_investigation`.
- Registro dinámico de especialistas y subgrafos.
- Proyección semántica compacta y versionada.
- Revisión LLM condicionada por riesgo.
- Caché Redis multinivel con namespace `v10`.
- Ruta AST-only componible para `LIMIT`, meses cerrados y ventanas móviles de días sobre SQL previamente aprobado, sin regeneración LLM.
- Preservación de seguridad, costo, presupuesto, HITL y ejecución read-only.
- Síntesis directa grounded y síntesis multi-evidencia.
- Trayectorias y evals agentic.
- UI, favicon e identidad visual vectorial y de alta resolución empaquetada.
- Reparación gobernada de errores SQL detectados por PostgreSQL `EXPLAIN`.
- Contratos semánticos por fuente, calendario `America/Lima` y ejemplos certificados en README.
- Provider Anthropic nativo con Messages API, presets Claude y Structured Outputs JSON Schema.
- Vista agregada de fallas de liquidación por comercio y métricas certificadas.

# Resultados

Validación ejecutada en el entorno de empaquetado:

```text
20 pruebas de contratos fuente aprobadas
0 pruebas de contratos fuente fallidas
Compilación Python (compileall) correcta
TOML válido
YAML válido
Scripts shell válidos
Assets de branding conservados
```

La suite completa no pudo ejecutarse en este entorno porque no están instalados `structlog`,
`langgraph`, `psycopg` ni `sqlglot`, y el índice de paquetes disponible no permitió instalarlos.
Las dependencias continúan declaradas en `pyproject.toml` y se instalan durante el build de la imagen
Docker. No se afirma una ejecución E2E con PostgreSQL, Redis, Streamlit o un proveedor LLM real.

# Pruebas añadidas

- Uvicorn se ejecuta con `--no-access-log` y el middleware propio omite `/health/*` por defecto.
- La configuración predeterminada habilita logs de workflow, LLM y consultas, pero mantiene el SQL redactado.
- Un grafo que termina con evidencia y `status=running` se recupera a `completed` desde el ledger.
- Un grafo que termina sin interrupción, evidencia ni estado terminal falla de forma explícita.
- SSE emite heartbeats sin cancelar la tarea pendiente del agente.
- Streamlit dispone de reconciliación acotada mediante `GET /api/v1/agent/runs/{runId}`.
- Un feedback puro de `LIMIT` no invoca al intérprete LLM.
- Un feedback compuesto de `LIMIT` y reducción/ampliación de meses cerrados no invoca al intérprete LLM.
- Una revisión temporal solo entra en la ruta rápida cuando el SQL anterior contiene una única ventana mensual cerrada verificable.
- La política impide reducir una ventana de un mes a cero meses.
- El detector de ruta rápida no clasifica solicitudes con cambios semánticos adicionales como revisiones estructurales.
- Una revisión `ast_only` omite la auto-revisión LLM incluso si el SQL previo tiene una forma compleja, manteniendo seguridad/costo/HITL.
- El subgrafo incluye el nodo `apply_deterministic_revision` y lo conecta condicionalmente: sin `final_sql` finaliza de forma cerrada antes de seguridad.
- Una ventana móvil de 30 días reconoce y aplica `+15 días`, produciendo 45 días sin regeneración LLM.
- Un seguimiento elíptico como «agrégale 15 a la búsqueda de liquidaciones» hereda la unidad únicamente cuando el AST anterior expone una sola ventana temporal gobernada.
- Una ejecución fallida conserva el último SQL válido y registra por separado el intento y el plan de revisión pendiente.
- Las memorias antiguas sin `last_sql` se recuperan desde el último payload estructurado válido de la sesión, sin analizar texto libre.
- Los presets Anthropic se cargan sin parámetros incompatibles y usan JSON Schema.
- El adaptador Anthropic envía `system`, `output_config.format`, `thinking` y `effort`, y omite `temperature`, `top_p` y `top_k`.
- La reserva de costo de una propuesta reparada reemplaza el candidato anterior.
- Los contratos por fuente rechazan columnas de otras vistas semánticas cuando SQLGlot está disponible.
- Los ejemplos certificados seleccionan la vista agregada de liquidación y los límites de ayer en `America/Lima`.
- El gate de costo conserva la primera causa accionable para mostrarla en la interfaz.
- El contexto proyectado conserva allowlist y políticas y reduce tamaño.
- Una propuesta simple evita revisión LLM adicional.
- Riesgos semánticos, SQL o de costo activan revisión LLM.
- Varios indicadores publicados no fuerzan por sí solos una revisión LLM.
- El payload de revisión elimina el árbol completo de `EXPLAIN` y conserva sus resúmenes.
- El router adaptativo usa Redis y evita repetir la llamada al modelo.
- El grafo contiene ruta directa y ruta completa.
- El núcleo adaptativo no contiene ramas específicas por especialista configurado.
- El namespace de caché está versionado.
- El favicon y el logo visible se cargan desde assets locales vectoriales y PNG de alta resolución.
- El catálogo normaliza correctamente la carpeta `entities` a tipo `entity`, por lo que publica dimensiones y fuentes al generador.
- `transaction_timestamp` está publicado en la vista, catálogo y ejemplo gobernado de últimas transacciones.
- El ejemplo no inventa `execution_timestamp` ni el estado `EXECUTED`.
- El generador recibe el SQL fallido y feedback explícito para repararlo sin repetir identificadores rechazados.
- PostgreSQL convierte errores de planificación determinísticos en una validación reintentable cuando `psycopg` está disponible.
- El README contiene al menos diez consultas listas para copiar.
- Los evals verifican modo adaptativo y cantidad máxima de revisiones LLM.
- La compilación real del grafo se ejecuta cuando LangGraph está instalado.
- `sql_repair` recibe únicamente el SQL fallido, feedback del validador, contrato vigente y contratos de fuente utilizados.
- `sql_revision` recibe el SQL aprobado y el plan tipado, sin historial ni documentos de retrieval.
- El generador inicial limita a tres los contratos de fuente candidatos y conserva la allowlist completa para seguridad.
- La reserva de salida se reduce a 2,200 tokens para generación, 1,800 para revisión y 1,400 para reparación.

# Medición de contexto

Medición reproducible sobre el catálogo incluido, usando una solicitud analítica genérica:

```text
Contexto completo:    48,329 caracteres (~13,808 tokens estimados)
Contexto proyectado:  19,026 caracteres (~5,436 tokens estimados)
Contexto de revisión: 12,968 caracteres (~3,705 tokens estimados)

Proyección / completo: 39.37%
Revisión / completo:   26.83%
```

Comando:

```bash
python scripts/measure_context_projection.py \
  --domain acquiring \
  --question "consulta agregada de indicadores para un periodo" \
  --focus "métricas certificadas"
```

La reducción depende del catálogo, la pregunta y los límites configurados; no es un porcentaje fijo.
El script ahora incluye `source_contracts` y `calendar_context`, por lo que la medición refleja el
payload semántico real y no una versión parcial.

## Medición del ciclo SQL por etapa

Medición sintética sobre el catálogo de adquirencia incluido:

```text
Generación inicial: 6,650 tokens de entrada estimados + 2,200 de salida reservada = 8,850
Revisión tipada:    2,024 tokens de entrada estimados + 1,800 de salida reservada = 3,824
Reparación SQL:     1,002 tokens de entrada estimados + 1,400 de salida reservada = 2,402
```

La reparación cabe dentro del presupuesto incluso después de una generación inicial y una revisión,
sin elevar `max_llm_tokens=24000`. Los valores reales dependen de la pregunta, el SQL y el proveedor.

## Medición del payload de auto-revisión

Prueba local sintética con un árbol `EXPLAIN` de 80 nodos y filtros extensos:

```text
Payload anterior con EXPLAIN: 208,796 caracteres (~59,656 tokens estimados)
Payload compacto nuevo:         4,886 caracteres (~1,396 tokens estimados)
Reducción medida:               97.66%
```

La medición demuestra el peor patrón corregido; el porcentaje real depende del plan generado por
PostgreSQL. Los controles determinísticos de costo siguen recibiendo el `EXPLAIN` completo.

# Eval agentic offline

El caso `simple_governed_query` obtuvo:

```text
score: 1.0
modo direct_specialist observado
0 revisiones LLM de propuesta
seguridad, costo y HITL antes de SQL
hallazgos vinculados a evidencia
sin acciones fuera de autoridad
```

# Dependencias no disponibles en el entorno de empaquetado

La ejecución completa de pytest se detuvo durante la colección por dependencias runtime ausentes:

- `structlog`: procesamiento estructurado de logs.
- `langgraph`: grafo, checkpoints e interrupciones HITL.
- `psycopg`: integración PostgreSQL.
- `sqlglot`: parsing y transformaciones AST.

Estas dependencias están declaradas en `pyproject.toml`. La validación completa recomendada se debe
ejecutar dentro de la imagen construida del API:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  run --rm api pytest -q
```

# Limitaciones del entorno

Docker/Podman, PostgreSQL, Redis, Streamlit, LangGraph, SQLGlot y credenciales de proveedores LLM
no están disponibles en el entorno de generación. Por ello no se ejecutó la interfaz ni el E2E
live completo. El proyecto
incluye:

```text
tests/unit/test_parent_graph_compilation.py
scripts/run_live_agentic_evals.py
datasets/evals/autonomous_society.yaml
```

Antes de promoción se debe ejecutar dentro de la imagen Docker:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm api pytest -q
python scripts/run_live_agentic_evals.py \
  --password "$BOOTSTRAP_PASSWORD" \
  --question "Investiga un comportamiento y sustenta la conclusión" \
  --output live-run.json
```

## Correcciones 0.9.12

- Se agregó logging estructurado configurable para HTTP, workflow, LLM, costo SQL, ejecución y persistencia.
- Los prompts y preguntas no se registran; se usan huellas SHA-256 truncadas para correlación.
- El texto SQL permanece desactivado por defecto mediante `LOG_SQL_TEXT=false`.
- Uvicorn desactiva su access log y el middleware propio omite `/health/live` y `/health/ready` cuando `LOG_HEALTH_CHECKS=false`.
- Los health checks de Docker y los endpoints continúan activos; solo se silencian sus accesos.
- SSE emite heartbeats configurables sin cancelar el `anext` pendiente del generador.
- Streamlit reconcilia runs durante hasta 240 segundos por defecto si el stream se corta, sin crear otra ejecución.
- Streamlit reconcilia el estado persistido si el stream termina antes del evento `completed`.
- El workflow impone una invariante terminal: recupera una respuesta desde `autonomous_evidence` o falla explícitamente.
- Se agregaron logs que confirman `workflow_graph_stream_finished`, `agent_run_response_persisted` y `agent_terminal_message_persisted`, permitiendo distinguir ejecución, respuesta y persistencia.
- La versión de aplicación y paquete sube a `0.9.12`; no cambia el esquema de base ni el namespace de caché.

## Correcciones 0.9.11

- El nodo de revisión determinística ya no enlaza incondicionalmente con seguridad: si la aplicación AST falla o no genera `final_sql`, finaliza con un error gobernado.
- `validate_security`, `estimate_cost` y la auto-revisión validan explícitamente la existencia del SQL y del contexto semántico antes de acceder al estado.
- Los cachés parciales o antiguos sin SQL, tarea o contexto completo se consideran `cache miss` y se reconstruyen.
- `change_time_window` soporta ventanas móviles verificables en días mediante literal numérico o `INTERVAL`, además de ventanas mensuales cerradas.
- «Agrégale 15 días a la búsqueda» transforma 30 días en 45 días conservando `LIMIT`, fuentes, agrupación, métricas y orden.
- Cuando el usuario omite la unidad, esta solo se hereda si el SQL anterior demuestra una única ventana gobernada y el mensaje no contiene semántica de límite o resultados.
- Una revisión fallida no reemplaza el último contrato analítico válido de `ConversationMemory`; los datos del intento se guardan en campos separados.
- Las sesiones afectadas por versiones anteriores recuperan el último SQL válido desde metadata JSON persistida, sin OCR, scraping ni inferencia LLM.
- El contrato de memoria sube a `schema_version=4`, resolución contextual a `context-resolution-v3`, propuestas a `specialist-proposal-v11` y caché a `v10`.

## Correcciones 0.9.10

- La generación inicial, revisión conversacional y reparación SQL se separan en `sql_generator`, `sql_revision` y `sql_repair`.
- Las reparaciones ya no reenvían historial, resultados, ejemplos ni documentos completos del catálogo.
- Las revisiones tipadas reutilizan el SQL aprobado y un contexto de contratos compacto.
- El contexto semántico limita los contratos candidatos a tres, manteniendo completa la allowlist usada por seguridad.
- Los límites de salida quedan en 2,200, 1,800 y 1,400 tokens respectivamente.
- El presupuesto por tarea permanece en 24,000 tokens; no se amplió para ocultar prompts redundantes.
- Los errores de presupuesto ahora muestran tokens consumidos, reservados, solicitados y disponibles.
- El contrato de contexto sube a `semantic-context-v7`, propuestas a `specialist-proposal-v10` y caché a `v9`.

## Correcciones 0.9.9

- El intérprete local construye planes estructurales compuestos, por ejemplo `set_limit(100)` más `change_time_window(delta_months=-1)`.
- La ventana temporal solo se modifica si SQLGlot demuestra una única ventana de meses calendario cerrados en el SQL previamente aprobado.
- El AST actualiza el `INTERVAL` mensual y el `LIMIT` sin regenerar métricas, dimensiones, filtros, agrupamiento, orden ni fuentes.
- El contrato de interpretación y `TimeWindowContext` se reconcilian con el nuevo número de meses.
- Las revisiones determinísticas fallan de manera cerrada si seguridad, costo o cumplimiento no aprueban; no reingresan silenciosamente al generador LLM.
- Se omiten el intérprete LLM, el generador SQL y la auto-revisión del especialista para esta ruta comprobable.
- El presupuesto de `acquiring` permanece en `24,000` tokens; no se amplió para compensar llamadas evitables.
- El contrato de caché de propuestas sube a `specialist-proposal-v9` y el namespace a `v8`.

## Correcciones 0.9.8

- Las solicitudes completas y no ambiguas de `LIMIT` se interpretan localmente sin consumir tokens.
- El SQL previamente aprobado se modifica mediante AST y conserva filtros, métricas, dimensiones, agrupación, orden y fuentes.
- Las revisiones `ast_only` omiten la regeneración SQL y la auto-revisión LLM redundante del especialista.
- Seguridad SQLGlot, `EXPLAIN`/costo, presupuestos e HITL continúan ejecutándose.
- El presupuesto de `acquiring` permanece en `24,000` tokens; no se amplió para compensar llamadas evitables.
- El contrato de caché de propuestas sube a `specialist-proposal-v8` y el namespace a `v7`.

# Conclusión

La validación disponible confirma contratos, routing, gobierno, caché, reducción de contexto,
trayectorias y empaquetado. No se afirma una validación runtime end-to-end con servicios reales en
este entorno.

## Correcciones 0.9.7

- El costo, filas y bytes de un candidato SQL reemplazan la reserva previa durante reparaciones; no se acumulan varios `EXPLAIN` de la misma propuesta.
- El gate de costo y seguridad devuelve una causa concreta y acotada, no solo un mensaje genérico.
- `semantic.v_merchant_settlement_metrics` responde rankings de fallas de liquidación sin escanear detalle transaccional.
- `semantic.v_decline_analysis` publica un contrato exacto y el ejemplo de ayer usa límites explícitos en `America/Lima`.
- Anthropic se integra con perfiles tipados, validación de catálogo, Structured Outputs y parámetros específicos por modelo.
- El nuevo logo generado de Axiz permanece empaquetado en alta resolución y se usa en Streamlit.
