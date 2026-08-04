from dataclasses import dataclass

import pytest

from ingestion.normalization_pipeline import (
    NormalizationContext,
    NormalizationPipeline,
)


@dataclass(frozen=True)
class MarkerTransformer:
    transformer_id: str
    order: int
    output_field: str

    def apply(self, context: NormalizationContext) -> None:
        before = context.normalized.get(self.output_field)
        context.normalized[self.output_field] = self.transformer_id
        context.record_change(
            transformer_id=self.transformer_id,
            target="normalized",
            field_name=self.output_field,
            rule_ids=(f"RULE-{self.order}",),
            before=before,
            after=self.transformer_id,
            confidence_effect=0.1,
        )


@dataclass(frozen=True)
class RawMutationTransformer:
    transformer_id: str = "raw-mutation"
    order: int = 10

    def apply(self, context: NormalizationContext) -> None:
        context.raw_record["manufacturer"] = "Changed"


def test_pipeline_runs_in_declared_order_and_produces_contiguous_trace() -> None:
    pipeline = NormalizationPipeline(
        version="test-v1",
        transformers=(
            MarkerTransformer("second", 20, "second_value"),
            MarkerTransformer("first", 10, "first_value"),
        ),
    )

    result = pipeline.run({"manufacturer": "Volvo"})

    assert [entry.transformer_id for entry in result.decision_trace] == [
        "first",
        "second",
    ]
    assert [entry.sequence for entry in result.decision_trace] == [1, 2]
    assert result.raw_record == {"manufacturer": "Volvo"}


def test_pipeline_rejects_transformers_that_mutate_raw_evidence() -> None:
    pipeline = NormalizationPipeline(
        version="test-v1",
        transformers=(RawMutationTransformer(),),
    )

    with pytest.raises(RuntimeError, match="mutated the raw record"):
        pipeline.run({"manufacturer": "Volvo"})


@pytest.mark.parametrize(
    ("first", "second", "message"),
    [
        (
            MarkerTransformer("duplicate", 10, "one"),
            MarkerTransformer("duplicate", 20, "two"),
            "IDs",
        ),
        (
            MarkerTransformer("one", 10, "one"),
            MarkerTransformer("two", 10, "two"),
            "orders",
        ),
    ],
)
def test_pipeline_rejects_ambiguous_transformer_configuration(
    first: MarkerTransformer,
    second: MarkerTransformer,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        NormalizationPipeline(version="test-v1", transformers=(first, second))


def test_trace_contract_rejects_sensitive_output_fields() -> None:
    context = NormalizationContext(raw_record={})

    with pytest.raises(ValueError, match="sensitive field"):
        context.record_change(
            transformer_id="unsafe",
            target="normalized",
            field_name="vin",
            rule_ids=("UNSAFE",),
            before=None,
            after="SENSITIVE",
            confidence_effect=0.0,
        )
