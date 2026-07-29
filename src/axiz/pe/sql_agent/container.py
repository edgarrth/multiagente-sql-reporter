from __future__ import annotations

from axiz.pe.sql_agent.agents import (
    EvidenceReviewerAgent,
    InvestigationCoordinatorAgent,
    SqlEngineerAgent,
)
from axiz.pe.sql_agent.skills.semantic_exploration import SemanticExplorationSkill
from axiz.pe.sql_agent.config import Settings
from axiz.pe.sql_agent.core.auth import PasswordService, TokenService
from axiz.pe.sql_agent.core.database import Database
from axiz.pe.sql_agent.core.redis_client import RedisStore
from axiz.pe.sql_agent.models.contracts import AutonomousBudget
from axiz.pe.sql_agent.query_engines.factory import QueryEngineFactory
from axiz.pe.sql_agent.repositories.conversation_memory_repository import (
    ConversationMemoryRepository,
)
from axiz.pe.sql_agent.repositories.run_repository import RunRepository
from axiz.pe.sql_agent.repositories.session_repository import SessionRepository
from axiz.pe.sql_agent.repositories.user_repository import UserRepository
from axiz.pe.sql_agent.services.agent_cache import AgentResponseCache
from axiz.pe.sql_agent.services.agent_skills import AgentSkillRegistry
from axiz.pe.sql_agent.services.auth_service import AuthService
from axiz.pe.sql_agent.services.conversation_memory import StructuredConversationMemoryService
from axiz.pe.sql_agent.services.llm import AgentModelRegistry, StructuredLLMFactory
from axiz.pe.sql_agent.services.model_validation import ModelCatalogValidator
from axiz.pe.sql_agent.services.run_execution import RunExecutionCoordinator
from axiz.pe.sql_agent.services.specialist_graph_registry import SpecialistGraphRegistry
from axiz.pe.sql_agent.services.specialist_registry import SpecialistRegistry
from axiz.pe.sql_agent.tools.chart_builder import ChartBuilderTool
from axiz.pe.sql_agent.tools.example_selector import ExampleSelectorTool
from axiz.pe.sql_agent.tools.excel_export import ExcelExportTool
from axiz.pe.sql_agent.tools.investigation_governance import InvestigationGovernancePolicy
from axiz.pe.sql_agent.tools.llm_token_estimator import LLMApprovalTokenEstimator
from axiz.pe.sql_agent.tools.semantic_catalog import SemanticCatalogTool
from axiz.pe.sql_agent.tools.semantic_context_projection import SemanticContextProjector
from axiz.pe.sql_agent.tools.proposal_review_policy import ProposalReviewPolicy
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.workflow.graph import build_graph
from axiz.pe.sql_agent.workflow.nodes import WorkflowNodes
from axiz.pe.sql_agent.workflow.service import AgentWorkflowService
from axiz.pe.sql_agent.workflow.subgraphs import EvidenceReviewSubgraphFactory, SpecialistSubgraphFactory


class ApplicationContainer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings)
        self.redis = RedisStore(settings.redis_url)
        self.agent_cache = AgentResponseCache(
            self.redis,
            namespace=settings.agent_cache_namespace,
            enabled=settings.agent_cache_enabled,
            default_ttl_seconds=settings.agent_cache_default_ttl_seconds,
        )

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
        self.agent_skill_registry = AgentSkillRegistry(settings.agent_skills_config_path)
        self.model_validator = ModelCatalogValidator(settings, self.model_registry)
        self.catalog = SemanticCatalogTool(settings.semantic_catalog_path)
        self.specialist_registry = SpecialistRegistry(
            settings.specialist_registry_path,
            self.catalog,
        )
        self.examples = ExampleSelectorTool(self.catalog)
        self.query_engine = QueryEngineFactory.create(settings)
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
        self.autonomous_budget = AutonomousBudget(
            max_iterations=settings.autonomous_max_iterations,
            max_tasks=settings.autonomous_max_tasks,
            max_parallel_tasks=settings.autonomous_max_parallel_tasks,
            max_queries=settings.autonomous_max_queries,
            max_llm_tokens=settings.autonomous_max_llm_tokens,
            max_active_execution_seconds=settings.autonomous_max_active_execution_seconds,
            max_total_plan_cost=settings.autonomous_max_total_plan_cost,
            max_total_plan_rows=settings.autonomous_max_total_plan_rows,
            max_total_relation_bytes=settings.autonomous_max_total_relation_bytes,
            max_total_database_seconds=settings.autonomous_max_total_database_seconds,
        )
        self.investigation_governance = InvestigationGovernancePolicy(
            self.autonomous_budget
        )

        # Four reasoning roles. Operation modes share one model identity per role.
        self.investigation_coordinator_agent = InvestigationCoordinatorAgent(
            self.llm_factory.for_agent("investigation_coordinator"),
            self.agent_cache,
            self.agent_skill_registry.get("investigation_coordinator"),
        )
        self.semantic_context_projector = SemanticContextProjector(
            max_catalog_documents=settings.semantic_context_max_documents,
            max_examples=settings.semantic_context_max_examples,
            max_metrics=settings.semantic_context_max_metrics,
            max_dimensions=settings.semantic_context_max_dimensions,
            max_document_items=settings.semantic_context_max_document_items,
            max_source_contracts=settings.semantic_context_max_source_contracts,
        )
        self.semantic_agent = SemanticExplorationSkill(
            self.catalog,
            self.examples,
            self.agent_cache,
            self.semantic_context_projector,
        )
        self.sql_engineer_agent = SqlEngineerAgent(
            self.llm_factory.for_agent("sql_engineer"),
            self.agent_skill_registry.get("sql_engineer"),
            dialect=self.query_engine.capabilities.dialect,
            max_result_rows=settings.max_result_rows,
        )
        self.evidence_reviewer_agent = EvidenceReviewerAgent(
            self.llm_factory.for_agent("evidence_reviewer"),
            self.charts,
            self.agent_skill_registry.get("evidence_reviewer"),
        )
        self.proposal_review_policy = ProposalReviewPolicy(
            self.query_engine.capabilities.dialect,
            high_cost_ratio=settings.autonomous_review_high_cost_ratio,
            high_row_ratio=settings.autonomous_review_high_row_ratio,
        )
        specialist_factory = SpecialistSubgraphFactory(
            semantic_agent=self.semantic_agent,
            sql_agent=self.sql_engineer_agent,
            security_validator=self.validator,
            query_engine=self.query_engine,
            cache=self.agent_cache,
            review_policy=self.proposal_review_policy,
            conditional_review_enabled=settings.autonomous_conditional_review_enabled,
            history_max_messages=settings.specialist_history_max_messages,
            history_max_chars=settings.specialist_history_max_chars,
            prior_evidence_max_items=settings.specialist_prior_evidence_max_items,
            prior_evidence_max_rows=settings.specialist_prior_evidence_max_rows,
        )
        self.specialist_graph_registry = SpecialistGraphRegistry(
            registry=self.specialist_registry,
            subgraph_factory=specialist_factory,
            llm_factory=self.llm_factory,
            model_registry=self.model_registry,
            agent_skill_registry=self.agent_skill_registry,
        )
        self.critic_subgraph = EvidenceReviewSubgraphFactory(self.evidence_reviewer_agent).build()

        self.memory_service = StructuredConversationMemoryService(
            settings.conversation_memory_result_sample_rows,
            self.query_engine.capabilities.dialect,
        )
        self.nodes = WorkflowNodes(
            settings=settings,
            investigation_coordinator_agent=self.investigation_coordinator_agent,
            sql_engineer_agent=self.sql_engineer_agent,
            evidence_reviewer_agent=self.evidence_reviewer_agent,
            specialist_graph_registry=self.specialist_graph_registry,
            critic_subgraph=self.critic_subgraph,
            specialist_registry=self.specialist_registry,
            investigation_governance=self.investigation_governance,
            semantic_agent=self.semantic_agent,
            charts=self.charts,
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
            max_llm_tokens=settings.autonomous_max_llm_tokens,
            active_execution_timeout_seconds=settings.autonomous_max_active_execution_seconds,
            log_workflow_stages=settings.log_workflow_stages,
        )

    def reload_specialists(self) -> list[dict]:
        """Reload profile metadata.

        Existing specialist IDs are refreshed immediately. Adding or removing a graph topology
        requires restarting the API so the parent LangGraph can be recompiled safely.
        """
        previous_roles = self.specialist_graph_registry.roles()
        self.specialist_graph_registry.reload()
        current_roles = self.specialist_graph_registry.roles()
        profiles = self.specialist_registry.available_for_planning()
        if previous_roles != current_roles:
            for item in profiles:
                item["topology_restart_required"] = True
        return profiles

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
