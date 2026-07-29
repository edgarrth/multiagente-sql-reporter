FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install '.[ui]'
COPY .streamlit ./.streamlit
COPY streamlit_app ./streamlit_app
CMD ["sh", "-c", "exec streamlit run streamlit_app/app.py --server.address=\"${STREAMLIT_HOST:?STREAMLIT_HOST is required}\" --server.port=\"${STREAMLIT_PORT:?STREAMLIT_PORT is required}\" --server.headless=true"]
