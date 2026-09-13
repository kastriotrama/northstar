"""`resolve()`'s synonym branch: fuel/bodywork/drive rows in the same
live table TecDoc's own gap resolutions use, distinguished by `source_system`
and validated differently -- no promoted-data gap to check the value
against, and a `compatible` row can name more than one target for the same
source term.
"""

import pytest

from api.app.features.tecdoc_review.gaps import TecDocResolveError
from api.app.features.tecdoc_review.service import TecDocReviewService


class FakeRepository:
    """Records what would be written; no real database involved."""

    def __init__(self) -> None:
        self.upserts: list[dict] = []
        self.compatible_inserts: list[dict] = []

    def upsert_resolution(self, **kwargs):
        self.upserts.append(kwargs)
        return {
            "decision": kwargs["decision"],
            "canonical_value": kwargs["canonical_value"],
            "note": kwargs["note"],
            "reviewed_by": kwargs["reviewed_by"],
            "updated_at": "2026-09-10T00:00:00+00:00",
        }

    def insert_compatible_resolution(self, **kwargs):
        self.compatible_inserts.append(kwargs)
        return {
            "decision": "accepted",
            "canonical_value": kwargs["canonical_value"],
            "note": kwargs["note"],
            "reviewed_by": kwargs["reviewed_by"],
            "updated_at": "2026-09-10T00:00:00+00:00",
        }


def _service() -> tuple[TecDocReviewService, FakeRepository]:
    repository = FakeRepository()
    return TecDocReviewService(repository), repository


def test_an_equivalent_ts_term_is_written_via_upsert_resolution() -> None:
    service, repository = _service()

    result = service.resolve(
        canonical_field="fuel",
        source_term="electricity",
        decision="accepted",
        canonical_value="electric",
        note="",
        reviewed_by="pytest",
        source_system="transportstyrelsen",
        relation="equivalent",
    )

    assert repository.upserts[0]["canonical_field"] == "fuel"
    assert repository.upserts[0]["source_system"] == "transportstyrelsen"
    assert repository.upserts[0]["relation"] == "equivalent"
    assert repository.upserts[0]["decision"] == "accepted"
    assert repository.compatible_inserts == []
    assert result.source_system == "transportstyrelsen"
    assert result.relation == "equivalent"
    assert result.support is None


def test_a_compatible_pair_is_written_via_insert_compatible_resolution() -> None:
    service, repository = _service()

    result = service.resolve(
        canonical_field="drive",
        source_term="2wd",
        decision="accepted",
        canonical_value="fwd",
        note="",
        reviewed_by="pytest",
        source_system="transportstyrelsen",
        relation="compatible",
        support=744197,
    )

    assert repository.compatible_inserts[0]["source_term"] == "2wd"
    assert repository.compatible_inserts[0]["canonical_value"] == "fwd"
    assert repository.compatible_inserts[0]["support"] == 744197
    assert repository.upserts == []
    assert result.relation == "compatible"
    assert result.support == 744197


def test_the_same_source_term_can_be_compatible_with_two_targets() -> None:
    """TS's undifferentiated 2wd is compatible with both TecDoc fwd and rwd."""

    service, repository = _service()

    service.resolve(
        canonical_field="drive", source_term="2wd", decision="accepted",
        canonical_value="fwd", note="", reviewed_by="pytest",
        source_system="transportstyrelsen", relation="compatible", support=744197,
    )
    service.resolve(
        canonical_field="drive", source_term="2wd", decision="accepted",
        canonical_value="rwd", note="", reviewed_by="pytest",
        source_system="transportstyrelsen", relation="compatible", support=744197,
    )

    targets = {call["canonical_value"] for call in repository.compatible_inserts}
    assert targets == {"fwd", "rwd"}


def test_compatible_without_support_is_rejected() -> None:
    service, _ = _service()

    with pytest.raises(TecDocResolveError, match="support"):
        service.resolve(
            canonical_field="fuel", source_term="ethanol", decision="accepted",
            canonical_value="petrol", note="", reviewed_by="pytest",
            source_system="transportstyrelsen", relation="compatible", support=None,
        )


def test_a_tecdoc_authored_term_cannot_be_marked_compatible() -> None:
    """Compatibility is directional -- only a coarser TS term can be compatible
    with a finer TecDoc one, never the reverse."""

    service, _ = _service()

    with pytest.raises(TecDocResolveError, match="compatible"):
        service.resolve(
            canonical_field="fuel", source_term="electric", decision="accepted",
            canonical_value="electricity", note="", reviewed_by="pytest",
            source_system="tecdoc", relation="compatible", support=10,
        )


def test_a_blank_canonical_value_is_rejected() -> None:
    service, _ = _service()

    with pytest.raises(TecDocResolveError, match="canonical term"):
        service.resolve(
            canonical_field="fuel", source_term="electricity", decision="accepted",
            canonical_value=None, note="", reviewed_by="pytest",
            source_system="transportstyrelsen", relation="equivalent",
        )


def test_a_blank_reviewer_is_rejected() -> None:
    service, _ = _service()

    with pytest.raises(TecDocResolveError, match="authoring"):
        service.resolve(
            canonical_field="bodywork", source_term="estate", decision="accepted",
            canonical_value="wagon", note="", reviewed_by="  ",
            source_system="tecdoc", relation="equivalent",
        )


def test_support_is_dropped_when_relation_is_equivalent() -> None:
    """A stray `support` on an equivalent request is not carried into storage
    or echoed back -- support is only ever meaningful for `compatible`."""

    service, repository = _service()

    result = service.resolve(
        canonical_field="fuel", source_term="methane", decision="accepted",
        canonical_value="cng", note="", reviewed_by="pytest",
        source_system="transportstyrelsen", relation="equivalent", support=99,
    )

    assert "support" not in repository.upserts[0]
    assert result.support is None
