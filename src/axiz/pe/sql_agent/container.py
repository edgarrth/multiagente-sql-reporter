from __future__ import annotations

from axiz.pe.sql_agent.agents.context_resolver_agent import ContextResolverAgent
from axiz.pe.sql_agent.agents.conversation_context_agent import ConversationContextAgent
from axiz.pe.sql_agent.agents.explanation_agent import ExplanationAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.agents.result_verifier_agent import ResultVerifierAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.core.auth import PasswordService, TokenService
from axiz.pe.sql_agent.core.database import Database
from axiz.pe.sql_agent.core.redis_client import RedisStore
from axiz.pe.sql_agent.repositories.conversation_memory_repository import (
    ConversationMemoryRepository,
)
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.repositories.user_repository import UserRepository
from axiz.pe.sql_agent.query_engines.factory import QueryEngineFactory
from axiz.pe.sql_agent.services.auth_service import AuthService
from axiz.pe.sql_agent.services.conversation_memory import StructuredConversationMemoryService
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLMFactory
from axiz.pe.sql_agent.services.model_validation import ModelCatalogValidator
from axiz.pe.sql_agent.services.run_execution import RunExecutionCoordinator
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool
from axiz.pe.sql_agent.tools.llm_token_estimator import LLMApprovalTokenEstimator
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
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
        self.memories = ConversationMemoryRepository(self.db)
        self.runs = RunRepository(self.db)

        self.passwords = PasswordService()
        self.tokens = TokenService(settings)
        self.auth = AuthService(settings, self.users, self.passwords, self.tokens)

        self.model_registry = AgentModelRegistry(
            settings.agent_models_config_path,
            default_provider=settings.llm_provider,
        )
        self.llm_factory = StructuredLLMFactory(settings, self.model_registry)
        self.model_validator = ModelCatalogValidator(settings, self.model_registry)
        self.catalog = SemanticCatalogTool(settings.semantic_catalog_path)
        self.examples = ExampleSelectorTool(self.catalog)
        self.query_engine = QueryEngineFactory.create(settings)
        # Compatibility alias for existing routes and integrations.
        self.query_tool = self.query_engine
        self.validator = SqlSecurityValidator(
            self.query_engine.capabilities.dialect, settings.max_result_rows
        )
        self.charts = ChartBuilderTool()
        self.excel_exports = ExcelExportTool(
            enabled=settings.excel_export_enabled,
            max_rows=settings.excel_export_max_rows,
            allow_truncated=settings.excel_export_allow_truncated,
        )
        self.llm_approval_estimator = LLMApprovalTokenEstimator(
            self.model_registry,
            settings.max_result_rows,
        )

        self.context_resolver_agent = ContextResolverAgent(
            self.llm_factory.for_agent("context_resolver")
        )
        self.intent_agent = IntentDomainAgent(self.llm_factory.for_agent("intent_domain"))
        self.conversation_agent = ConversationContextAgent(
            self.llm_factory.for_agent("conversation_context")
        )
        self.semantic_agent = SemanticExplorerAgent(self.catalog, self.examples)
        self.sql_agent = SqlGeneratorAgent(
            self.llm_factory.for_agent("sql_generator"),
            self.query_engine.capabilities.dialect,
        )
        self.verifier_agent = ResultVerifierAgent(
            self.llm_factory.for_agent("result_verifier")
        )
        self.explanation_agent = ExplanationAgent(
            self.llm_factory.for_agent("explanation"),
            self.llm_factory.for_agent("catalog_answer"),
            self.charts,
        )

        self.memory_service = StructuredConversationMemoryService(
            settings.conversation_memory_result_sample_rows,
            self.query_engine.capabilities.dialect,
        )

        self.nodes = WorkflowNodes(
            settings=settings,
            context_resolver_agent=self.context_resolver_agent,
            intent_agent=self.intent_agent,
            conversation_agent=self.conversation_agent,
            semantic_agent=self.semantic_agent,
            sql_agent=self.sql_agent,
            verifier_agent=self.verifier_agent,
            explanation_agent=self.explanation_agent,
            catalog=self.catalog,
            validator=self.validator,
            query_engine=self.query_engine,
            llm_approval_estimator=self.llm_approval_estimator,
            runs=self.runs,
        )
        self.graph_builder = build_graph(self.nodes)
        self.execution_coordinator = RunExecutionCoordinator(
            self.runs,
            lease_seconds=settings.run_lease_seconds,
            heartbeat_seconds=settings.run_lease_heartbeat_seconds,
        )
        self.workflow = AgentWorkflowService(
            checkpoint_dsn=settings.checkpoint_database_url,
            graph_builder=self.graph_builder,
            sessions=self.sessions,
            memories=self.memories,
            memory_service=self.memory_service,
            runs=self.runs,
            excel_exports=self.excel_exports,
            execution_coordinator=self.execution_coordinator,
            max_concurrent_runs_per_user=settings.max_concurrent_runs_per_user,
        )

    async def start(self) -> None:
        await self.auth.bootstrap()
        if self.settings.model_validation_on_startup:
            await self.model_validator.validate(force=True)
        await self.workflow.start()

    async def close(self) -> None:
        await self.workflow.close()
        await self.query_engine.close()
        await self.redis.close()
        await self.db.close()
