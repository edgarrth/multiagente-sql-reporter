# Validación técnica — Axiz SQL Agent PoC 0.11.5

# Alcance

Esta validación cubre la reparación idempotente de `.env`, la inyección de configuración en Docker
Compose sin interpolación temprana de secretos, el bootstrap seguro de usuarios, la interfaz
Streamlit, el desplazamiento automático del chat, la arquitectura de SQL autónomo, los cuatro
agentes, los contratos estructurados y los controles de seguridad.

# Comandos

```bash
python -m compileall -q src streamlit_app teams_adapter scripts tests
python scripts/audit_agent_autonomy.py
python scripts/check_internal_imports.py
python scripts/check_agent_wiring.py
PYTHONPATH=/tmp/axiz_test_stubs:src pytest tests/unit -q
```

Para preparar un entorno local reproducible:

```bash
python scripts/generate_local_env.py
python scripts/validate_env.py
```

# Controles verificados

- Los secretos de aplicación, contraseña bootstrap, clave interna y credenciales de base de datos
  no tienen valores reutilizables en el código ni en `.env.example`.
- Docker Compose no usa `${DATABASE_URL:?…}` ni otras interpolaciones obligatorias de secretos,
  por lo que el comando `build` puede analizar el archivo antes de inicializar la configuración.
- `generate_local_env.py` repara variables vacías y conserva valores existentes, incluidas las API
  keys; `validate_env.py` detecta campos vacíos, longitudes inseguras y URLs inválidas.
- La aplicación falla al iniciar cuando falta una variable obligatoria o se conserva un placeholder
  inseguro.
- `BOOTSTRAP_SYNC_CREDENTIALS` permite aplicar desde variables una nueva contraseña o nuevos roles
  al usuario local existente, sin reemplazar usuarios de otro proveedor de identidad.
- Docker Compose consume imágenes, puertos, nombres, usuarios, contraseñas, URLs, timeouts,
  healthchecks y políticas de reinicio desde `.env`.
- Streamlit consume URL de API, timeouts, zona horaria, textos principales y parámetros de
  auto-scroll desde variables de entorno.
- El auto-scroll instala un `MutationObserver` en el documento principal y reacciona a mensajes
  nuevos, reruns y actualizaciones incrementales recibidas por SSE.
- Solo existen cuatro clases de agentes de razonamiento.
- No existen módulos anteriores de feedback tipado o QuerySpec fijo.
- No hay regex de interpretación dentro de `agents/` o `skills/`.
- Seguridad conserva allowlists, solo lectura, columnas publicadas y límites.
- La API agrega tokens de todos los runs de una sesión.
- Streamlit muestra consumo acumulado y consumo por consulta.
- Todos los imports internos apuntan a módulos o símbolos empaquetados existentes.

# Resultado local

```text
160 pruebas unitarias aprobadas
7 pruebas omitidas por dependencias opcionales no disponibles
0 pruebas fallidas
```

También finalizaron correctamente las validaciones de sintaxis, imports internos, wiring de agentes
y auditoría de autonomía.

# Limitaciones del entorno de empaquetado

El entorno de empaquetado no dispone de Docker ni de todos los paquetes runtime. Para las pruebas
que solo necesitaban logging se utilizó un stub temporal externo de `structlog`; dicho stub no está
incluido en el proyecto. No se ejecutaron llamadas reales a un proveedor LLM ni un E2E Docker con
PostgreSQL y Redis.
