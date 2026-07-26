import os

import pytest

psycopg = pytest.importorskip("psycopg")


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_CONTROL_DSN"),
    reason="Set TEST_CONTROL_DSN to run control-plane PostgreSQL integration tests",
)


def test_control_database_contains_conversation_tables_only() -> None:
    with psycopg.connect(os.environ["TEST_CONTROL_DSN"]) as connection:
        assert connection.execute("SELECT current_database()").fetchone()[0] == "axiz_agent_control"

        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'app'
                """
            ).fetchall()
        }
        assert {
            "users",
            "chat_sessions",
            "chat_messages",
            "agent_runs",
            "session_memory",
            "human_feedback",
            "audit_events",
            "channel_sessions",
        }.issubset(tables)

        semantic_schema_exists = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'semantic')"
        ).fetchone()[0]
        assert semantic_schema_exists is False
