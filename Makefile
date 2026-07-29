ENV_FILE ?= .env
COMPOSE_FILE ?= infrastructure/docker-compose.yml
API_HOST ?= 0.0.0.0
API_PORT ?= 8000
STREAMLIT_HOST ?= 0.0.0.0
STREAMLIT_PORT ?= 8501
PACKAGE_NAME ?= axiz-pe-sql-agent-poc.zip
PROJECT_DIR ?= axiz-pe-sql-agent-poc

.PHONY: env env-check build up check-ollama pull-ollama down reset logs test lint run-api run-ui package

env:
	python scripts/generate_local_env.py --output $(ENV_FILE)

env-check:
	python scripts/validate_env.py --env-file $(ENV_FILE)

build: env-check
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) build --no-cache api streamlit

up: env-check
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up --build -d

check-ollama:
	./scripts/check_ollama_host.sh

pull-ollama:
	./scripts/pull_ollama_models.sh

down:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down

reset:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down -v

logs:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f

test:
	pytest -q --cov=axiz.pe.sql_agent --cov-report=term-missing

lint:
	ruff check src tests streamlit_app teams_adapter scripts

run-api:
	uvicorn axiz.pe.sql_agent.main:app --reload --host $(API_HOST) --port $(API_PORT)

run-ui:
	streamlit run streamlit_app/app.py --server.port $(STREAMLIT_PORT) --server.address $(STREAMLIT_HOST)

package:
	cd .. && zip -r $(PACKAGE_NAME) $(PROJECT_DIR) -x '$(PROJECT_DIR)/.venv/*' '*/__pycache__/*'
