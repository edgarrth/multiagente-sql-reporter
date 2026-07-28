from pathlib import Path

import pytest

from axiz.pe.sql_agent.models.query_spec import (
    CompiledSqlArtifact,
    FilterBooleanOperator,
    QuerySpecPatch,
    QuerySpecPatchOperation,
    SemanticDimension,
    SemanticFilterGroup,
    SemanticMeasure,
    SemanticOrder,
    SemanticPredicate,
    SemanticQuerySpec,
    SemanticTimeFilter,
    SemanticTimeRange,
)
from axiz.pe.sql_agent.services.semantic_query_spec import SemanticQuerySpecService


def _base_spec() -> SemanticQuerySpec:
    return SemanticQuerySpec(
        spec_id="qs-payments",
        version=6,
        semantic_model="semantic.v_payment_transactions",
        original_question="Qué comercios tuvieron mayor facturación",
        raw_user_message="cambia la búsqueda para que sean transacciones rechazadas",
        measures=[
            SemanticMeasure(
                member="processed_amount_pen",
                alias="processed_amount_pen",
                aggregation="sum",
            )
        ],
        dimensions=[
            SemanticDimension(member="merchant_id", alias="merchant_id"),
            SemanticDimension(member="merchant_name", alias="merchant_name"),
        ],
        order_by=[
            SemanticOrder(member="processed_amount_pen", direction="desc"),
            SemanticOrder(member="merchant_name", direction="asc"),
        ],
        limit=50,
        source_objects=["semantic.v_payment_transactions"],
    )


def test_metric_patch_derives_dependent_order_change() -> None:
    service = SemanticQuerySpecService()
    base = _base_spec()
    patch = QuerySpecPatch(
        base=base.reference,
        raw_user_message="usa cantidad de transacciones rechazadas",
        operations=[
            QuerySpecPatchOperation(
                change_id="change_1",
                operation="replace",
                target="metric",
                from_member="processed_amount_pen",
                to_member="declined_transaction_count",
            )
        ],
        preserve=["dimensions", "time_filters", "limit", "source_objects"],
    )

    resolution = service.apply_patch(base, patch)

    assert resolution.resolved.version == 7
    assert [item.member for item in resolution.resolved.measures] == [
        "declined_transaction_count"
    ]
    assert [item.member for item in resolution.resolved.order_by] == [
        "declined_transaction_count",
        "merchant_name",
    ]
    assert resolution.resolved.limit == 50
    assert resolution.derived_changes[0].target == "order"
    assert resolution.derived_changes[0].from_member == "processed_amount_pen"
    assert resolution.derived_changes[0].to_member == "declined_transaction_count"


def test_nested_filter_patch_preserves_unrelated_filters() -> None:
    service = SemanticQuerySpecService()
    base = _base_spec().model_copy(
        update={
            "filters": SemanticFilterGroup(
                operator=FilterBooleanOperator.AND,
                expressions=[
                    SemanticPredicate(
                        member="transactions.status",
                        operator="equals",
                        values=["APPROVED"],
                    ),
                    SemanticFilterGroup(
                        operator=FilterBooleanOperator.OR,
                        expressions=[
                            SemanticPredicate(
                                member="merchant.city",
                                operator="equals",
                                values=["Lima"],
                            ),
                            SemanticPredicate(
                                member="merchant.risk_level",
                                operator="in",
                                values=["HIGH", "CRITICAL"],
                            ),
                        ],
                    ),
                ],
            )
        }
    )
    patch = QuerySpecPatch(
        base=base.reference,
        raw_user_message="cambia el estado a DECLINED",
        operations=[
            QuerySpecPatchOperation(
                change_id="change_1",
                operation="replace",
                target="filter",
                member="transactions.status",
                from_member="transactions.status",
                values=["DECLINED"],
                predicate_operator="equals",
            )
        ],
    )

    resolved = service.apply_patch(base, patch).resolved
    predicates = service._predicates(resolved.filters)
    values_by_member = {item.member: item.values for item in predicates}

    assert values_by_member["transactions.status"] == ["DECLINED"]
    assert values_by_member["merchant.city"] == ["Lima"]
    assert values_by_member["merchant.risk_level"] == ["HIGH", "CRITICAL"]


def test_multiple_time_members_require_an_explicit_target() -> None:
    service = SemanticQuerySpecService()
    base = _base_spec().model_copy(
        update={
            "time_filters": [
                SemanticTimeFilter(
                    member="transactions.transaction_date",
                    range=SemanticTimeRange(type="relative", unit="day", value=14),
                    timezone="America/Lima",
                ),
                SemanticTimeFilter(
                    member="transactions.settlement_date",
                    range=SemanticTimeRange(type="relative", unit="day", value=5),
                    timezone="America/Lima",
                ),
            ]
        }
    )
    patch = QuerySpecPatch(
        base=base.reference,
        raw_user_message="aumenta siete días",
        operations=[
            QuerySpecPatchOperation(
                change_id="change_1",
                operation="increase",
                target="time_window",
                value=7,
                unit="days",
                scope="overall",
            )
        ],
    )

    with pytest.raises(ValueError, match="multiple time filters"):
        service.apply_patch(base, patch)


def test_patch_transports_reference_and_delta_while_state_retains_full_spec() -> None:
    base = _base_spec()
    patch = QuerySpecPatch(
        base=base.reference,
        raw_user_message="limita los resultados a 25",
        operations=[
            QuerySpecPatchOperation(
                change_id="change_1",
                operation="set",
                target="limit",
                value=25,
            )
        ],
    )

    serialized = patch.model_dump(mode="json")

    assert serialized["base"] == {"id": "qs-payments", "version": 6}
    assert serialized["raw_user_message"] == "limita los resultados a 25"
    assert "measures" not in serialized
    assert "dimensions" not in serialized
    resolved = SemanticQuerySpecService().apply_patch(base, patch).resolved
    assert resolved.limit == 25
    assert resolved.measures == base.measures
    assert resolved.dimensions == base.dimensions


def test_compiled_artifact_has_explicit_execution_state() -> None:
    artifact = CompiledSqlArtifact(
        query_spec_ref=_base_spec().reference,
        dialect="postgres",
        sql="SELECT 1",
        sql_hash="sha256:test",
        execution_state="candidate",
    )

    assert artifact.execution_state == "candidate"


def test_streamlit_distinguishes_candidate_from_executed_sql() -> None:
    source = Path("streamlit_app/app.py").read_text(encoding="utf-8")

    assert 'sql_title = "SQL ejecutado"' in source
    assert 'sql_title = "SQL candidato no ejecutado"' in source
    assert "execution_state == \"executed\"" in source


def test_compiled_sql_validation_rejects_obsolete_order_alias() -> None:
    pytest.importorskip("sqlglot")
    service = SemanticQuerySpecService()
    spec = _base_spec().model_copy(
        update={
            "measures": [
                SemanticMeasure(
                    member="declined_transaction_count",
                    alias="declined_transaction_count",
                    aggregation="count",
                )
            ],
            "order_by": [
                SemanticOrder(member="declined_transaction_count", direction="desc")
            ],
        }
    )
    sql = """
    SELECT merchant_id,
           merchant_name,
           COUNT(*) FILTER (WHERE status = 'DECLINED') AS declined_transaction_count
      FROM semantic.v_payment_transactions
     GROUP BY merchant_id, merchant_name
     ORDER BY processed_amount_pen DESC
     LIMIT 50
    """

    validation = service.validate_compiled_sql(
        sql,
        source_contracts={
            "semantic.v_payment_transactions": {
                "columns": [
                    "merchant_id",
                    "merchant_name",
                    "status",
                ]
            }
        },
        spec=spec,
    )

    assert validation["order_dependencies_valid"] is False
    assert "processed_amount_pen" in validation["invalid_order_references"]
    assert validation["query_spec_alignment_valid"] is False


def test_failed_candidate_is_forwarded_to_repair_mode() -> None:
    from axiz.pe.sql_agent.models.contracts import FeedbackComplianceResult

    result = FeedbackComplianceResult(
        compliant=False,
        retry_instruction="replace the obsolete ORDER BY alias",
        failed_sql="SELECT declined_transaction_count ORDER BY processed_amount_pen",
    )

    assert result.failed_sql is not None
    generation = Path("src/axiz/pe/sql_agent/skills/sql/generation.py").read_text(
        encoding="utf-8"
    )
    nodes = Path("src/axiz/pe/sql_agent/workflow/nodes.py").read_text(encoding="utf-8")
    assert 'compliance.get("failed_sql")' in generation
    assert '"failed_sql": state.get("generated_sql") or ""' in nodes



def test_time_window_is_not_duplicated_as_generic_filter() -> None:
    service = SemanticQuerySpecService()
    spec = service.from_contract(
        {
            "selected_metrics": [],
            "selected_dimensions": [
                "transaction_id",
                "merchant_name",
                "response_code",
            ],
            "selected_filters": [
                {
                    "field": "status",
                    "operator": "equals",
                    "value": "DECLINED",
                },
                {
                    "field": "transaction_date",
                    "operator": "greater_than_or_equal",
                    "value": "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date - 30",
                },
                {
                    "field": "transaction_date",
                    "operator": "less_than",
                    "value": "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date",
                },
            ],
            "time_window": [
                {
                    "member": "transaction_date",
                    "range": {
                        "type": "relative",
                        "unit": "day",
                        "value": 30,
                        "exclude_current_period": True,
                    },
                    "timezone": "America/Lima",
                }
            ],
            "limit": 10,
            "source_objects": ["semantic.v_payment_transactions"],
        },
        original_question=(
            "Muéstrame las 10 últimas transacciones rechazadas "
            "con comercio y código de respuesta"
        ),
    )

    predicates = service._predicates(spec.filters)

    assert spec.schema_version == "1.1"
    assert [(item.member, item.values) for item in predicates] == [
        ("status", ["DECLINED"])
    ]
    assert len(spec.time_filters) == 1
    assert spec.time_filters[0].member == "transaction_date"
    assert spec.time_filters[0].range.value == 30


def test_persisted_spec_is_migrated_away_from_duplicate_date_predicates() -> None:
    service = SemanticQuerySpecService()
    persisted = SemanticQuerySpec(
        schema_version="1.0",
        spec_id="qs-declined-transactions",
        dimensions=[
            SemanticDimension(member="transaction_id", alias="transaction_id"),
            SemanticDimension(member="merchant_name", alias="merchant_name"),
            SemanticDimension(member="response_code", alias="response_code"),
        ],
        filters=SemanticFilterGroup(
            operator=FilterBooleanOperator.AND,
            expressions=[
                SemanticPredicate(
                    member="status",
                    operator="equals",
                    values=["DECLINED"],
                ),
                SemanticPredicate(
                    member="transaction_date",
                    operator="greater_than_or_equal",
                    values=[
                        "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date - 30"
                    ],
                ),
                SemanticPredicate(
                    member="transaction_date",
                    operator="less_than",
                    values=[
                        "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date"
                    ],
                ),
            ],
        ),
        time_filters=[
            SemanticTimeFilter(
                member="transaction_date",
                range=SemanticTimeRange(type="relative", unit="day", value=30),
                timezone="America/Lima",
            )
        ],
        limit=10,
        source_objects=["semantic.v_payment_transactions"],
    )

    migrated = service.from_contract({"query_spec": persisted.model_dump(mode="json")})
    predicates = service._predicates(migrated.filters)

    assert migrated.schema_version == "1.1"
    assert [(item.member, item.values) for item in predicates] == [
        ("status", ["DECLINED"])
    ]
    assert migrated.version == persisted.version


def test_equivalent_postgres_date_renderings_do_not_fail_alignment() -> None:
    pytest.importorskip("sqlglot")
    service = SemanticQuerySpecService()
    spec = service.from_contract(
        {
            "selected_dimensions": [
                "transaction_id",
                "merchant_name",
                "response_code",
            ],
            "selected_filters": [
                {
                    "field": "status",
                    "operator": "equals",
                    "value": "DECLINED",
                },
                {
                    "field": "transaction_date",
                    "operator": ">=",
                    "value": "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date - 30",
                },
                {
                    "field": "transaction_date",
                    "operator": "<",
                    "value": "(TIMEZONE('America/Lima', CURRENT_TIMESTAMP))::date",
                },
            ],
            "time_window": [
                {
                    "member": "transaction_date",
                    "range": {
                        "type": "relative",
                        "unit": "day",
                        "value": 30,
                    },
                    "timezone": "America/Lima",
                }
            ],
            "ordering": [
                {"field": "transaction_timestamp", "direction": "desc"}
            ],
            "limit": 10,
            "source_objects": ["semantic.v_payment_transactions"],
        }
    )
    sql = """
    SELECT transaction_id, merchant_name, response_code
      FROM semantic.v_payment_transactions
     WHERE status = 'DECLINED'
       AND transaction_date >= CAST(
             TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE
           ) - 30
       AND transaction_date < CAST(
             TIMEZONE('America/Lima', CURRENT_TIMESTAMP) AS DATE
           )
     ORDER BY transaction_timestamp DESC
     LIMIT 10
    """

    validation = service.validate_compiled_sql(
        sql,
        source_contracts={
            "semantic.v_payment_transactions": {
                "columns": [
                    "transaction_id",
                    "merchant_name",
                    "response_code",
                    "status",
                    "transaction_date",
                    "transaction_timestamp",
                ]
            }
        },
        spec=spec,
    )

    assert validation["query_spec_alignment_valid"] is True
    assert validation["query_spec_violations"] == []
