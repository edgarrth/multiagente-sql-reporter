FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .
COPY semantic_catalog ./semantic_catalog
COPY config ./config
CMD ["sh", "-c", "exec uvicorn axiz.pe.sql_agent.main:app --host \"${API_HOST:?API_HOST is required}\" --port \"${API_PORT:?API_PORT is required}\" --no-access-log"]
