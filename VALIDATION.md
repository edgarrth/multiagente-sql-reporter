# Validación técnica — Axiz SQL Agent PoC 0.9.4

# Alcance validado

- Routing autónomo adaptativo `direct_specialist` / `full_investigation`.
- Registro dinámico de especialistas y subgrafos.
- Proyección semántica compacta y versionada.
- Revisión LLM condicionada por riesgo.
- Caché Redis multinivel con namespace `v3`.
- Preservación de seguridad, costo, presupuesto, HITL y ejecución read-only.
- Síntesis directa grounded y síntesis multi-evidencia.
- Trayectorias y evals agentic.
- UI, favicon e identidad visual vectorial y de alta resolución empaquetada.
- Reparación gobernada de errores SQL detectados por PostgreSQL `EXPLAIN`.
- Contrato semántico de transacciones recientes y ejemplos de consultas en README.

# Resultados

```text
136 pruebas aprobadas
19 pruebas omitidas
0 pruebas fallidas

Compilación Python correcta
TOML válido
YAML válido
Scripts shell válidos
Eval agentic offline: score 1.0
```

# Pruebas añadidas

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

# Medición de contexto

Medición reproducible sobre el catálogo incluido, usando una solicitud analítica genérica:

```text
Contexto completo:    29,329 caracteres (~8,380 tokens estimados)
Contexto proyectado:   9,903 caracteres (~2,829 tokens estimados)
Contexto de revisión:  3,645 caracteres (~1,041 tokens estimados)

Proyección / completo: 33.77%
Revisión / completo:   12.43%
```

Comando:

```bash
python scripts/measure_context_projection.py \
  --domain acquiring \
  --question "consulta agregada de indicadores para un periodo" \
  --focus "métricas certificadas"
```

La reducción depende del catálogo, la pregunta y los límites configurados; no es un porcentaje fijo.

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

# Pruebas omitidas

Las 19 pruebas omitidas requieren dependencias no instaladas en el entorno de empaquetado:

- `psycopg`: integración PostgreSQL, repositorios, contratos de persistencia y prueba runtime del manejo de `UndefinedColumn`.
- `sqlglot`: parsing y transformaciones AST reales.
- `langgraph`: compilación runtime del grafo padre.

Estas dependencias están declaradas en `pyproject.toml` y se instalan en la imagen Docker de la API.

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

# Conclusión

La validación disponible confirma contratos, routing, gobierno, caché, reducción de contexto,
trayectorias y empaquetado. No se afirma una validación runtime end-to-end con servicios reales en
este entorno.
