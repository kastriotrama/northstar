"""Bodywork rulings reach the matcher; TecDoc's own KT086 code rulings stay separate."""

from contextlib import nullcontext

from api.app.features.tecdoc_review.repository import TecDocReviewRepository
from ingestion.tecdoc.canonical_rule_proposals import comparison_key
from ingestion.tecdoc.resolution_migrations import (
    TECDOC_RESOLUTION_RULES_TABLE,
    run_tecdoc_resolution_migrations,
)
from ingestion.vocabulary_alignment import load_bodywork_alignment
from tests.integration.throwaway_database import throwaway_database


def test_ts_body_pairs_load_and_tecdoc_code_rulings_stay_out() -> None:
    with throwaway_database("bodywork_alignment") as connection:
        run_tecdoc_resolution_migrations(connection)
        with connection.cursor() as cursor:
            # TecDoc's own body ruling: a KT086 code to a canonical body.
            cursor.execute(
                f"INSERT INTO {TECDOC_RESOLUTION_RULES_TABLE} (canonical_field, comparison_key, "
                "source_term, key_table, decision, canonical_value, note, reviewed_by) "
                "VALUES ('bodywork_form', '043', '043', '086', 'accepted', 'coupe', '', 'pytest')"
            )
        connection.commit()
        repository = TecDocReviewRepository(lambda: nullcontext(connection))
        for body in ("sedan", "hatchback"):
            repository.insert_compatible_resolution(
                canonical_field="bodywork", comparison_key=comparison_key("covered_body"),
                source_term="covered_body", canonical_value=body, support=10,
                note="closed body", reviewed_by="pytest",
            )

        alignment = load_bodywork_alignment(connection)

    assert alignment.compatible_pairs == frozenset(
        {("covered_body", "sedan"), ("covered_body", "hatchback")}
    )
    assert alignment.tecdoc_equivalences == {}
