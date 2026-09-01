from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from psycopg import Connection

from ingestion.match_chunks import (
    ChunkBuildError,
    build_match_chunks,
    chunk_id_for,
    compute_signature,
    signature_key,
)


def _payload(**normalized: Any) -> dict[str, Any]:
    return {"normalized": normalized, "candidates": {}}


def test_signature_mirrors_matcher_fields_and_sorts_fuels() -> None:
    signature = compute_signature(
        _payload(
            manufacturer=" Volvo ",
            model_family="V70",
            energy_sources=["petrol", "electric", "petrol"],
            production_year=2014,
            engine_code="B4204T",
            displacement_cc="1969",
            power_kw=132,
            drive_type="FWD",
            bodywork_form="estate",
        )
    )

    assert signature["manufacturer"] == "Volvo"
    assert signature["energy_sources"] == ["electric", "petrol"]
    assert signature["displacement_cc"] == 1969
    assert signature["signature_version"] == "2"


def test_signature_falls_back_to_candidates_for_identity_fields() -> None:
    signature = compute_signature(
        {
            "normalized": {"manufacturer": None},
            "candidates": {"manufacturer": "Saab", "model_family": "9-3"},
        }
    )

    assert signature["manufacturer"] == "Saab"
    assert signature["model_family"] == "9-3"


def test_signature_key_is_order_independent_and_value_sensitive() -> None:
    first = compute_signature(_payload(manufacturer="Volvo", power_kw=132))
    reordered = dict(reversed(list(first.items())))
    different = compute_signature(_payload(manufacturer="Volvo", power_kw=140))

    assert signature_key(first) == signature_key(reordered)
    assert signature_key(first) != signature_key(different)


def test_chunk_id_is_deterministic_per_build() -> None:
    build_a = UUID("00000000-0000-0000-0000-000000000001")
    build_b = UUID("00000000-0000-0000-0000-000000000002")
    key = signature_key(compute_signature(_payload(manufacturer="Volvo")))

    assert chunk_id_for(build_a, key) == chunk_id_for(build_a, key)
    assert chunk_id_for(build_a, key) != chunk_id_for(build_b, key)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_batch_prefix": "  "},
        {"statuses": ()},
        {"statuses": ("review_required", " ")},
        {"page_size": 0},
    ],
)
def test_build_rejects_unsafe_inputs_before_touching_the_database(
    kwargs: dict[str, Any],
) -> None:
    arguments: dict[str, Any] = {
        "build_id": uuid4(),
        "source_batch_prefix": "batch-1",
        **kwargs,
    }
    with pytest.raises(ChunkBuildError):
        build_match_chunks(cast(Connection[Any], object()), **arguments)


def test_signature_defers_to_the_matcher_key_when_one_is_supplied() -> None:
    """Rows the matcher separates must not share a chunk.

    The v1 signature read model_family from the normalized payload and stopped.
    Model recovery runs after that, so rows with no model_family but different
    recovered models -- a Golf and a Sharan both recovered from brand -- looked
    identical to chunking while the matcher evaluated them apart.
    """

    from ingestion.match_chunks import compute_signature, signature_key

    golf = {"normalized": {"manufacturer": "VW"}, "candidates": {}}
    sharan = {"normalized": {"manufacturer": "VW"}, "candidates": {}}

    # Without the matcher key these are indistinguishable: the defect.
    assert signature_key(compute_signature(golf)) == signature_key(compute_signature(sharan))

    keys = {id(golf): ("VW", "GOLF"), id(sharan): ("VW", "SHARAN")}
    resolver = lambda payload: keys[id(payload)]

    assert signature_key(
        compute_signature(golf, evaluation_key=resolver)
    ) != signature_key(compute_signature(sharan, evaluation_key=resolver))


def test_signature_falls_back_when_the_row_has_no_matcher_key() -> None:
    """A row that terminates before matching keeps the normalized-field grouping."""

    from ingestion.match_chunks import compute_signature

    payload = {"normalized": {"manufacturer": "VOLVO", "model_family": "V70"}, "candidates": {}}
    signature = compute_signature(payload, evaluation_key=lambda _payload: None)

    assert signature["manufacturer"] == "VOLVO"
    assert signature["model_family"] == "V70"
    assert "evaluation_key" not in signature
