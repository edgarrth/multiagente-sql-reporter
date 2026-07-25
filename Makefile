.PHONY: up check-ollama pull-ollama down reset logs test lint run-api run-ui package

up:
	docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d

check-ollama:
	./scripts/check_ollama_host.sh

pull-ollama:
	./scripts/pull_ollama_models.sh

down:
	docker compose --env-file .env -f infrastructure/docker-compose.yml down

reset:
	docker compose --env-file .env -f infrastructure/docker-compose.yml down -v

logs:
	docker compose --env-file .env -f infrastructure/docker-compose.yml logs -f

test:
	pytest -q --cov=axiz.pe.sql_agent --cov-report=term-missing

lint:
	ruff check src tests streamlit_app teams_adapter

run-api:
	uvicorn axiz.pe.sql_agent.main:app --reload --host 0.0.0.0 --port 8000

run-ui:
	streamlit run streamlit_app/app.py --server.port 8501 --server.address 0.0.0.0

package:
	cd .. && zip -r axiz-pe-sql-agent-poc.zip axiz-pe-sql-agent-poc -x 'axiz-pe-sql-agent-poc/.venv/*' '*/__pycache__/*'
