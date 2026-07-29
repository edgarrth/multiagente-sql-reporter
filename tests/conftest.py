"""Deterministic non-production configuration for the offline test suite."""

from __future__ import annotations

import os


_TEST_ENV = {
    "APP_SECRET_KEY": "test-app-secret-key-that-is-long-enough-123456",
    "BOOTSTRAP_USERNAME": "test-admin",
    "BOOTSTRAP_PASSWORD": "TestBootstrapPassword123!",
    "BOOTSTRAP_ROLES": '["admin","analyst"]',
    "INTERNAL_SERVICE_KEY": "test-internal-service-key-1234567890",
    "DATABASE_URL": "postgresql+psycopg://owner:test@localhost:5432/control",
    "CHECKPOINT_DATABASE_URL": "postgresql://owner:test@localhost:5432/control",
    "AGENT_DATABASE_URL": "postgresql://reader:test-password@localhost:5432/business",
    "REDIS_URL": "redis://localhost:6379/0",
    "CORS_ORIGINS": '["http://localhost:8501"]',
    "STREAMLIT_API_BASE_URL": "http://api.test:8000",
}

for key, value in _TEST_ENV.items():
    os.environ.setdefault(key, value)
