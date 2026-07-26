from __future__ import annotations

from axiz.pe.sql_agent.agents.autonomous import (
    AutonomousComplexityRouterAgent,
    AutonomousSupervisorAgent,
    CriticAgent,
    InvestigationPlannerAgent,
)
from axiz.pe.sql_agent.agents.context_resolver_agent import ContextResolverAgent
from axiz.pe.sql_agent.agents.conversation_context_agent import ConversationContextAgent
from axiz.pe.sql_agent.agents.explanation_agent import ExplanationAgent
from axiz.pe.sql_agent.agents.feedback_compliance_agent import FeedbackComplianceAgent
from axiz.pe.sql_agent.agents.feedback_interpreter_agent import FeedbackInterpreterAgent
from axiz.pe.sql_agent.agents.intent_domain_agent import IntentDomainAgent
from axiz.pe.sql_agent.agents.result_verifier_agent import ResultVerifierAgent
from axiz.pe.sql_agent.agents.semantic_explorer_agent import SemanticExplorerAgent
from axiz.pe.sql_agent.agents.sql_generator_agent import SqlGeneratorAgent
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
from axiz.pe.sql_agent.tools.sql_feedback import SqlFeedbackApplier
from axiz.pe.sql_agent.tools.sql_feedback_compliance import SqlFeedbackComplianceValidator
from axiz.pe.sql_agent.tools.sql_feedback_plan import SqlFeedbackPlanValidator
from axiz.pe.sql_agent.tools.sql_security import SqlSecurityValidator
from axiz.pe.sql_agent.workflow.graph import build_graph
from axiz.pe.sql_agent.workflow.nodes import WorkflowNodes
from axiz.pe.sql_agent.workflow.service import AgentWorkflowService
from axiz.pe.sql_agent.workflow.subgraphs import CriticSubgraphFactory, SpecialistSubgraphFactory


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
        self.sql_feedback_applier = SqlFeedbackApplier(
            self.query_engine.capabilities.dialect, settings.max_result_rows
        )
        self.feedback_compliance_validator = SqlFeedbackComplianceValidator(
            self.query_engine.capabilities.dialect
        )
        self.feedback_plan_validator = SqlFeedbackPlanValidator()
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

        # Shared agents and deterministic tools used by the specialist subgraphs.
        self.context_resolver_agent = ContextResolverAgent(
            self.llm_factory.for_agent("context_resolver"), self.agent_cache
        )
        self.intent_agent = IntentDomainAgent(
            self.llm_factory.for_agent("intent_domain"), self.agent_cache
        )
        self.conversation_agent = ConversationContextAgent(
            self.llm_factory.for_agent("conversation_context")
        )
        self.semantic_context_projector = SemanticContextProjector(
            max_catalog_documents=settings.semantic_context_max_documents,
            max_examples=settings.semantic_context_max_examples,
            max_metrics=settings.semantic_context_max_metrics,
            max_dimensions=settings.semantic_context_max_dimensions,
            max_document_items=settings.semantic_context_max_document_items,
        )
        self.semantic_agent = SemanticExplorerAgent(
            self.catalog,
            self.examples,
            self.agent_cache,
            self.semantic_context_projector,
        )
        self.sql_agent = SqlGeneratorAgent(
            self.llm_factory.for_agent("sql_generator"),
            self.query_engine.capabilities.dialect,
            settings.max_result_rows,
        )
        self.feedback_interpreter_agent = FeedbackInterpreterAgent(
            self.llm_factory.for_agent("feedback_interpreter"),
            settings.max_result_rows,
            self.feedback_plan_validator,
        )
        self.feedback_compliance_agent = FeedbackComplianceAgent(
            self.llm_factory.for_agent("feedback_compliance")
        )
        self.verifier_agent = ResultVerifierAgent(
            self.llm_factory.for_agent("result_verifier")
        )
        self.explanation_agent = ExplanationAgent(
            self.llm_factory.for_agent("explanation"),
            self.llm_factory.for_agent("catalog_answer"),
            self.charts,
        )

        # Autonomous society agents. Routing/planning/delegation are agentic; authority remains outside.
        self.autonomous_router_agent = AutonomousComplexityRouterAgent(
            self.llm_factory.for_agent("autonomous_router"), self.agent_cache
        )
        self.investigation_planner_agent = InvestigationPlannerAgent(
            self.llm_factory.for_agent("investigation_planner"), self.agent_cache
        )
        self.autonomous_supervisor_agent = AutonomousSupervisorAgent(
            self.llm_factory.for_agent("autonomous_supervisor"),
            self.llm_factory.for_agent("autonomous_synthesis"),
        )
        self.critic_agent = CriticAgent(self.llm_factory.for_agent("critic_agent"))
        self.proposal_review_policy = ProposalReviewPolicy(
            self.query_engine.capabilities.dialect,
            high_cost_ratio=settings.autonomous_review_high_cost_ratio,
            high_row_ratio=settings.autonomous_review_high_row_ratio,
        )
        specialist_factory = SpecialistSubgraphFactory(
            semantic_agent=self.semantic_agent,
            sql_agent=self.sql_agent,
            feedback_interpreter=self.feedback_interpreter_agent,
            feedback_applier=self.sql_feedback_applier,
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
        )
        self.critic_subgraph = CriticSubgraphFactory(self.critic_agent).build()

        self.memory_service = StructuredConversationMemoryService(
            settings.conversation_memory_result_sample_rows,
            self.query_engine.capabilities.dialect,
        )
        self.nodes = WorkflowNodes(
            settings=settings,
            context_resolver_agent=self.context_resolver_agent,
            autonomous_router_agent=self.autonomous_router_agent,
            autonomous_supervisor_agent=self.autonomous_supervisor_agent,
            investigation_planner_agent=self.investigation_planner_agent,
            specialist_graph_registry=self.specialist_graph_registry,
            critic_subgraph=self.critic_subgraph,
            specialist_registry=self.specialist_registry,
            investigation_governance=self.investigation_governance,
            intent_agent=self.intent_agent,
            conversation_agent=self.conversation_agent,
            semantic_agent=self.semantic_agent,
            sql_agent=self.sql_agent,
            feedback_interpreter_agent=self.feedback_interpreter_agent,
            feedback_compliance_agent=self.feedback_compliance_agent,
            verifier_agent=self.verifier_agent,
            explanation_agent=self.explanation_agent,
            charts=self.charts,
            catalog=self.catalog,
            validator=self.validator,
            sql_feedback_applier=self.sql_feedback_applier,
            feedback_compliance_validator=self.feedback_compliance_validator,
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
