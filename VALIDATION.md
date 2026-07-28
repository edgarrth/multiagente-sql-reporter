# Validación técnica — Axiz SQL Agent PoC 0.10.6

Fecha de validación: 2026-07-27.

# Alcance

Se validó la generación autónoma guiada por catálogo y la ruta SQL nativa de revisión. La cobertura incluye:

- una solicitud top-N no requiere un rango temporal inventado;
- continuidad conversacional después de un intento fallido sin SQL aprobado;
- política temporal opt-in mediante `enforce_temporal_filter`;
- seguridad y costo conservados mediante `LIMIT`, allowlist y `EXPLAIN`;
- entrega del mensaje original y de la sentencia SQL completa al modo `revise`;
- eliminación del vocabulario cerrado como requisito previo para revisar filtros, proyección u orden;
- diff AST genérico de proyección, fuentes, filtros, agrupación, `HAVING`, orden, límite y `DISTINCT`;
- reconstrucción del snapshot semántico desde la sentencia final, no desde propiedades antiguas;
- preservación de controles de catálogo, seguridad, costo, presupuesto y HITL;
- normalización temporal heredada de 0.10.4;
- contratos cerrados para Structured Outputs;
- `CompiledSqlArtifact` como SQL validado con hash y estado de ejecución.

# Arquitectura

Validaciones realizadas:

- `agents/` contiene únicamente cuatro identidades de agente:
  - `InvestigationCoordinatorAgent`;
  - `DomainAnalystAgent`;
  - `SqlEngineerAgent`;
  - `EvidenceReviewerAgent`.
- Los perfiles de dominio parametrizan `DomainAnalystAgent` y no agregan identidades LLM.
- `agents/` y `skills/` no contienen expresiones regulares para interpretar intención o feedback.
- La capa LLM interpreta lenguaje natural y produce contratos estructurados.
- SQLGlot se utiliza como analizador y reescritor técnico del SQL cuando está disponible en runtime.
- La ejecución, seguridad, costo, presupuesto, HITL, persistencia y auditoría permanecen en servicios determinísticos.

# Revisión SQL nativa

Se verificó que:

- `FeedbackPlanningSkill` crea un envelope genérico con el mensaje íntegro, estrategia
  `regenerate` y una lista de cambios tipados vacía;
- no realiza una segunda llamada LLM para clasificar el feedback en targets fijos;
- `SqlGenerationSkill._revise` recibe `previous_sql`, `raw_user_feedback` y contratos acotados de
  fuentes;
- el agente devuelve la sentencia completa y puede solicitar una aclaración mediante
  `requires_clarification`;
- `SqlRevisionDiffAnalyzer` detecta columnas eliminadas, columnas agregadas, cambio de posición,
  filtros, agrupaciones, orden, límite, fuentes y `DISTINCT`;
- `SemanticQuerySpecService.from_sql_snapshot` deriva proyección, fuentes, orden y límite desde el
  AST final y conserva un identificador/versionado para memoria y auditoría;
- una propiedad obsoleta del estado anterior no puede reinsertar una columna eliminada;
- los artefactos SQL mantienen estados explícitos: `candidate`, `validated`,
  `awaiting_approval`, `executed`, `rejected` y `failed`.

# Regresión de Structured Outputs

Se verificó que `SqlGenerationOutput.model_json_schema()` ya no expone `compiled_sql_artifact`. También se recorren recursivamente todos los contratos usados como salida LLM para detectar nodos `object` con `additionalProperties` abierto. El guard se ejecuta antes de reservar presupuesto y antes de invocar OpenAI, Anthropic u Ollama.

El artefacto compilado se crea después de recibir el SQL:

```text
LLM -> SqlGenerationOutput cerrado
    -> SemanticQuerySpecService
    -> CompiledSqlArtifact + CompiledSqlValidation
    -> seguridad -> costo -> HITL
```

# Casos de regresión corregidos

## Ventana temporal duplicada

Se cubre la consulta “Muéstrame las 10 últimas transacciones rechazadas con comercio y código de respuesta”. Si el LLM entrega límites temporales tanto en `selected_filters` como en `time_window`, el resolvedor conserva una sola representación en `time_filters`. Los filtros resultantes mantienen `status = DECLINED`, pero no vuelven a validar por igualdad textual las expresiones `CAST(... AS DATE)` y `::date`.

## Alias de orden obsoleto


Se agregó cobertura para evitar esta combinación inconsistente:

```sql
SELECT COUNT(*) AS declined_transaction_count
...
ORDER BY processed_amount_pen DESC
```

Cuando la métrica `processed_amount_pen` se reemplaza por `declined_transaction_count`, el resolvedor deriva la actualización del ordenamiento. Si el SQL generado todavía contiene una referencia obsoleta, el artefacto se marca inválido y el candidato pasa a reparación; no alcanza costo, HITL ni ejecución.

# Suite automatizada

Ejecución en el entorno disponible:

```text
202 pruebas aprobadas
25 pruebas omitidas
0 pruebas fallidas
```

Las 25 pruebas omitidas requieren dependencias runtime no instaladas en el entorno de empaquetado:

- `sqlglot` para pruebas AST;
- `langgraph` para compilación y recuperación del grafo;
- `psycopg` y PostgreSQL para integración de base de datos.

Estas dependencias están declaradas en `pyproject.toml` y se instalan durante el build de la imagen Docker.

Para ejecutar la suite se utilizó un stub temporal de `structlog` ubicado fuera del proyecto, porque esa dependencia no está instalada en el runtime de empaquetado. El stub no forma parte del ZIP. Las pruebas omitidas corresponden a dependencias runtime no disponibles (`sqlglot`, `langgraph`, `psycopg`/PostgreSQL).

# Validaciones estáticas

- Compilación de `src/`, `tests/`, `streamlit_app/` y `teams_adapter/` con `compileall`: aprobada.
- 21 archivos YAML: parseados correctamente y sin claves duplicadas.
- 2 archivos TOML: parseados correctamente.
- 13 bloques JSON del README: parseados correctamente.
- Scripts shell: `bash -n` aprobado.
- Configuración de cuatro agentes: aprobada.
- Endpoint de contratos `/api/v1/models/query-spec-contracts`: presente.
- Contrato de caché de propuestas `specialist-proposal-v18`: presente.
- Ausencia de regex de interpretación en agentes y skills: aprobada.
- Assets del logo y favicon: presentes.

# Validación del paquete

El ZIP final se valida mediante:

- eliminación previa de `__pycache__`, `.pyc` y `.pytest_cache`;
- `unzip -t` sin errores;
- extracción en un directorio limpio;
- compilación del contenido extraído;
- parsing de YAML, TOML y bloques JSON del README desde la extracción;
- comprobación de cuatro clases de agente;
- comprobación de ausencia de regex de intención en `agents/` y `skills/`;
- comprobación de ausencia del stub temporal y cachés;
- cálculo SHA-256.

# Limitaciones

No se ejecutó un E2E real contra OpenAI, Anthropic u Ollama porque no se proporcionaron credenciales. Tampoco se levantaron Docker, PostgreSQL y Redis en este entorno. Por tanto, la validación no afirma que se haya realizado una llamada real a un proveedor LLM ni una ejecución completa sobre la infraestructura integrada.


# Validaciones específicas de 0.10.6

- Primera consulta top-N se enruta como solicitud independiente sin llamar al resolvedor de dependencia.
- `Dame las 20 últimas transacciones ejecutadas` no requiere fecha ni inventa estado `EXECUTED`.
- Un seguimiento a un intento fallido puede convertirse en generación nueva sin SQL anterior.
- La seguridad solo exige fecha cuando `enforce_temporal_filter=true`.
- El mensaje de aclaración ya no afirma que toda ambigüedad sea temporal.
- Feedback abierto se transporta como mensaje original + SQL completo, no como vocabulario cerrado.
- Diff AST genérico de proyección, filtros, agrupación, orden, límite, fuentes y DISTINCT.
- Caso de regresión: quitar `amount_pen` y mover `channel` antes de `city`.
- La especificación canónica se reconstruye desde el SQL revisado antes del gate de seguridad.
