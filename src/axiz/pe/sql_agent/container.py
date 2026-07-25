from __future__ import annotations

from axiz.pe.sql_agent.agents.explanation_agent import ExplanationAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.agents.result_verifier_agent import ResultVerifierAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.core.auth import PasswordService, TokenService
from axiz.pe.sql_agent.core.database import Database
from axiz.pe.sql_agent.core.redis_client import RedisStore
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.repositories.user_repository import UserRepository
from axiz.pe.sql_agent.services.auth_service import AuthService
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLMFactory
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.sql_executor import PostgresQueryTool
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.workflow.graph import build_graph
from axiz.pe.sql_agent.workflow.nodes import WorkflowNodes
from axiz.pe.sql_agent.workflow.service import AgentWorkflowService


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.redis = RedisStore(settings.redis_url)

        self.users = UserRepository(self.db)
        self.sessions = SessionRepository(self.db)
        self.runs = RunRepository(self.db)

        self.passwords = PasswordService()
        self.tokens = TokenService(settings)
        self.auth = AuthService(settings, self.users, self.passwords, self.tokens)

        self.model_registry = AgentModelRegistry(
            settings.agent_models_config_path,
            default_provider=settings.llm_provider,
        )
        self.llm_factory = StructuredLLMFactory(settings, self.model_registry)
        self.catalog = SemanticCatalogTool(settings.semantic_catalog_path)
        self.examples = ExampleSelectorTool(self.catalog)
        self.validator = SqlSecurityValidator(settings.sql_dialect, settings.max_result_rows)
        self.query_tool = PostgresQueryTool(
            settings.agent_database_url.get_secret_value(),
            timeout_seconds=settings.sql_timeout_seconds,
            max_rows=settings.max_result_rows,
            max_plan_rows=settings.max_plan_rows,
            max_plan_cost=settings.max_plan_cost,
            max_relation_bytes=settings.max_relation_bytes,
            connect_timeout_seconds=settings.agent_database_connect_timeout_seconds,
        )
        self.charts = ChartBuilderTool()

        self.intent_agent = IntentDomainAgent(self.llm_factory.for_agent("intent_domain"))
        self.semantic_agent = SemanticExplorerAgent(self.catalog, self.examples)
        self.sql_agent = SqlGeneratorAgent(
            self.llm_factory.for_agent("sql_generator"),
            settings.sql_dialect,
        )
        self.verifier_agent = ResultVerifierAgent(
            self.llm_factory.for_agent("result_verifier")
        )
        self.explanation_agent = ExplanationAgent(
            self.llm_factory.for_agent("explanation"),
            self.llm_factory.for_agent("catalog_answer"),
            self.charts,
        )

        self.nodes = WorkflowNodes(
            settings=settings,
            intent_agent=self.intent_agent,
            semantic_agent=self.semantic_agent,
            sql_agent=self.sql_agent,
            verifier_agent=self.verifier_agent,
            explanation_agent=self.explanation_agent,
            catalog=self.catalog,
            validator=self.validator,
            query_tool=self.query_tool,
            runs=self.runs,
        )
        self.graph_builder = build_graph(self.nodes)
        self.workflow = AgentWorkflowService(
            checkpoint_dsn=settings.checkpoint_database_url,
            graph_builder=self.graph_builder,
            sessions=self.sessions,
            runs=self.runs,
        )

    async def start(self) -> None:
        await self.auth.bootstrap()
        await self.workflow.start()

    async def close(self) -> None:
        await self.workflow.close()
        await self.redis.close()
        await self.db.close()
