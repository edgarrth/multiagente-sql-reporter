# Infraestructura local

La carpeta levanta PostgreSQL, bootstrap idempotente, Redis, API, Streamlit y el adaptador opcional
de Microsoft Teams.

# Servicios

| Servicio | Función |
|---|---|
| `postgres` | Control plane y datos sintéticos de la PoC |
| `postgres-bootstrap` | Crea usuarios, bases, vistas semánticas y datos iniciales |
| `redis` | Caché y coordinación |
| `api` | Sociedad autónoma gobernada y API REST/SSE |
| `streamlit` | Interfaz web |
| `teams` | Adaptador opcional mediante profile |


# Configuración obligatoria

Docker Compose no contiene contraseñas reutilizables. La configuración de ejecución se inyecta a
los contenedores mediante `env_file`, por lo que `docker compose build` no intenta interpolar
`DATABASE_URL` ni otros secretos.

Antes de levantar el stack, crea o repara y valida `.env`:

```bash
python scripts/generate_local_env.py
python scripts/validate_env.py
```

Si `.env` ya existe, el generador conserva los valores no vacíos —incluidas las API keys— y completa
secretos o URLs que estén en blanco. El archivo queda fuera de Git. Para producción, sustituye el
archivo local por variables inyectadas desde el gestor de secretos y la plataforma de despliegue.

# Variables de modelo

La arquitectura 0.11.2 utiliza cuatro perfiles:

```dotenv
AXIZ_INVESTIGATION_COORDINATOR_MODEL_PRESET=openai_gpt_5_6_terra_balanced
AXIZ_DOMAIN_ANALYST_MODEL_PRESET=openai_gpt_5_6_luna_routing
AXIZ_SQL_ENGINEER_MODEL_PRESET=openai_gpt_5_6_terra_sql
AXIZ_EVIDENCE_REVIEWER_MODEL_PRESET=openai_gpt_5_6_luna_explanation
AGENT_CACHE_NAMESPACE=axiz:agent-cache:v19
```

Para Anthropic:

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<api-key>
AXIZ_INVESTIGATION_COORDINATOR_MODEL_PRESET=anthropic_claude_sonnet_5_balanced
AXIZ_DOMAIN_ANALYST_MODEL_PRESET=anthropic_claude_haiku_4_5_routing
AXIZ_SQL_ENGINEER_MODEL_PRESET=anthropic_claude_sonnet_5_sql
AXIZ_EVIDENCE_REVIEWER_MODEL_PRESET=anthropic_claude_sonnet_5_explanation
```

# Levantar

```bash
python scripts/generate_local_env.py
python scripts/validate_env.py
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  up --build -d
```

# Logs

```bash
docker compose \
  --env-file .env \
  -f infrastructure/docker-compose.yml \
  logs -f api streamlit
```

Los health checks permanecen activos, pero no se registran cuando:

```dotenv
LOG_HEALTH_CHECKS=false
```

# Reinicio por migración desde 0.9.x

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml down
docker compose --env-file .env -f infrastructure/docker-compose.yml build --no-cache api streamlit
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d
```

No es necesario eliminar los volúmenes de PostgreSQL o Redis. Los runs que estuvieran en progreso
antes del cambio de topología deben iniciarse nuevamente.

# Error `DATABASE_URL is required`

Ese mensaje indica que `.env` fue copiado desde `.env.example` pero sus valores derivados continúan
en blanco. En 0.11.5 se corrige sin borrar el archivo ni perder las API keys existentes:

```bash
python scripts/generate_local_env.py
python scripts/validate_env.py
docker compose --env-file .env -f infrastructure/docker-compose.yml build --no-cache api streamlit
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d
```
