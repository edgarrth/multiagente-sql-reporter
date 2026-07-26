# Validación técnica — Axiz SQL Agent PoC 0.9.1

# Incidente corregido

La API fallaba durante el startup al construir el grafo padre:

```text
TypeError: StateGraph.add_conditional_edges() takes from 3 to 4 positional arguments but 5 were given
```

La llamada de `human_review` contenía accidentalmente dos funciones de routing. Se corrigió para
utilizar únicamente `route_after_review` y su mapa de destinos.

# Controles de regresión añadidos

- Validación AST de todas las llamadas a `add_conditional_edges`.
- Prueba de compilación de la topología real con la versión de LangGraph instalada.
- Consistencia de versión entre `pyproject.toml`, FastAPI y README.
- Validación del parche sobre una extracción limpia de 0.9.0.
- Validación del ZIP mediante `unzip -t` y extracción limpia.

# Validaciones ejecutadas en este entorno

```text
Compilación Python de src, Streamlit, Teams, scripts y tests: correcta
pyproject.toml: válido
YAML de configuración, catálogo y evals: válido
Scripts shell: sintaxis válida
Contrato AST de add_conditional_edges: correcto
La llamada human_review usa 3 argumentos posicionales: correcto
Parche aplicado sobre una copia limpia de 0.9.0: correcto
ZIP extraído y verificado: correcto
Sin .git, .env, __pycache__, .pytest_cache ni *.pyc: correcto
```

# Limitación de este entorno

No fue posible ejecutar aquí la prueba runtime de compilación con el paquete LangGraph real ni el
stack Docker end-to-end, porque este entorno no dispone de Docker/Podman y su índice Python no
expone LangGraph ni Hatchling. La prueba runtime queda incluida en la suite y debe ejecutarse dentro
de la imagen Docker de la API, donde las dependencias del proyecto sí se instalan.

Comando de aceptación recomendado:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml build --no-cache api
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm api \
  pytest tests/unit/test_parent_graph_compilation.py -q
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d
curl --fail http://localhost:8000/health/ready
```

# Conclusión

La causa exacta del crash de startup fue corregida y quedó cubierta por controles estáticos y una
prueba runtime de compilación. La aceptación final debe completarse en el runtime Docker del
proyecto para verificar la versión instalada de LangGraph, PostgreSQL, Redis y el checkpointer.
