# Validación técnica — Axiz SQL Agent PoC 0.9.0

# Alcance

La validación cubre la reconstrucción de la solución como sociedad autónoma gobernada:

- Grafo padre LangGraph con supervisor, planner, crítico y síntesis.
- Subgrafos aislados por especialista y registro dinámico mediante YAML.
- Fan-out paralelo acotado con reducers y olas de ejecución.
- Gates determinísticos de seguridad, costo, HITL, autoridad y presupuesto.
- Límites globales y por tarea, incluyendo reserva concurrente de tokens.
- Caché Redis con revalidación obligatoria de propuestas.
- Ledger multi-evidencia, hallazgos enlazados y exportación Excel.
- Evals de trayectoria y runner end-to-end para un ambiente desplegado.
- Paridad funcional con el workflow gobernado anterior.

# Resultado de la suite disponible en este entorno

```text
119 pruebas aprobadas
17 pruebas omitidas
0 pruebas fallidas
```

Las 17 omisiones corresponden a dependencias de runtime no instaladas en el entorno de generación:

- 13 casos que requieren SQLGlot.
- 4 casos que requieren Psycopg/PostgreSQL.

La suite incluye pruebas de:

- Registro y extensibilidad de especialistas.
- Límites globales y por tarea.
- Reserva atómica de tokens durante fan-out paralelo.
- Revalidación de propuestas obtenidas desde caché.
- Prohibición de ejecutar una propuesta con seguridad, costo o auto-revisión fallida.
- Evals de trayectoria, orden de gates y grounding de hallazgos.
- Exportación Excel de múltiples evidencias.
- Consistencia de versión entre paquete, API y README.
- Conservación de capacidades del proyecto anterior.

# Evals agentic offline

Se ejecutó `scripts/run_agentic_evals.py` sobre una trayectoria sintética completa del caso
`simple_governed_query`.

```text
passed: true
score: 1.0
required_action_sequence: true
no_forbidden_authority_actions: true
security_cost_hitl_before_execution: true
per_task_limits: true
findings_grounded: true
```

Los evaluadores comprueban comportamiento observable y no chain-of-thought.

# Validaciones estáticas y de configuración

```text
Compilación de src, Streamlit, Teams, scripts y tests: correcta
pyproject.toml: válido
config/agents.yaml: válido
config/specialists.yaml: válido
datasets/evals/autonomous_society.yaml: válido
infrastructure/docker-compose.yml: válido
Scripts shell: bash -n correcto
git diff --check: sin errores
```

Configuración descubierta:

```text
18 perfiles LLM configurados
1 dominio semántico publicado en la PoC: acquiring
3 especialistas SQL ejecutables: acquiring, chargebacks, temporal
1 crítico obligatorio: critic
issuing y fraud permanecen deshabilitados hasta publicar sus dominios semánticos
```

# Validación de artefactos

El ZIP final se valida mediante:

- `unzip -t`.
- Extracción en un directorio limpio.
- Ejecución de la suite desde la extracción.
- Compilación Python desde la extracción.
- Revisión de que no contenga `.git`, `.env`, `__pycache__`, `.pytest_cache` ni archivos `.pyc`.

El parche se valida aplicándolo sobre una extracción limpia de la versión 0.8.0 y ejecutando
nuevamente suite, compilación y validación de configuración.

# Validación end-to-end live incluida

El repositorio incluye `scripts/run_live_agentic_evals.py`, que utiliza la API real, crea una sesión,
procesa todos los HITL y persiste el `RunResponse` para evaluarlo con el dataset agentic.

No se ejecutó el stack live dentro del entorno de generación porque aquí no están disponibles:

- Docker/Podman.
- PostgreSQL y Redis de integración.
- LangGraph, SQLGlot y Psycopg instalados localmente.
- Credenciales de un proveedor OpenAI/Ollama.

Por tanto, no se afirma una validación live con modelos y bases reales. El comando debe ejecutarse en
un ambiente de integración antes de promover la solución:

```bash
python scripts/run_live_agentic_evals.py \
  --password "$BOOTSTRAP_PASSWORD" \
  --question "Investiga una variación y sustenta cada conclusión" \
  --output live-run.json

python scripts/run_agentic_evals.py \
  live-run.json \
  --case simple_governed_query
```

# Conclusión

El código, contratos, políticas, cache, presupuestos, exportación, pruebas offline y evaluadores
agentic quedan validados en el alcance disponible. La aceptación de runtime debe completarse con el
runner live en un stack Docker o Kubernetes de integración con proveedores y datos configurados.
