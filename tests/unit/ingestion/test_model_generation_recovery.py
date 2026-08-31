import pytest

from ingestion.fuzzy_matching import ManufacturerCandidateIndex, VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator


def catalog():
    return (
        VehicleCandidate("1", "VOLVO", "V70 II", model_aliases=("V70",), year_from=2000, year_to=2007),
        VehicleCandidate("2", "VOLVO", "V70 III", model_aliases=("V70",), year_from=2008, year_to=2016),
    )


def test_multiple_generations_recover_family_not_arbitrary_generation():
    assert ManufacturerCandidateIndex(catalog()).recover_model_from_evidence(
        "VOLVO", {"brand": "VOLVO S + V70"}
    ) == ("V70", "brand")


def test_recovered_family_without_distinguishing_evidence_stays_unresolved():
    result = TecDocDryRunEvaluator(catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VOLVO"}, "source_evidence": {"brand": "VOLVO V70"},
    }))
    assert result.terminal == "review_required"
    assert "model_evidence_missing" not in result.reason_codes
    assert "route:candidate_margin_below_gate" in result.reason_codes


def test_recovered_family_can_use_existing_year_gate():
    result = TecDocDryRunEvaluator(catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VOLVO", "production_year": 2010},
        "source_evidence": {"brand": "VOLVO V70"},
    }))
    assert result.top_candidate_reference == "2"
    assert result.terminal == "resolved"


def test_shared_trim_cannot_recover_unrelated_family():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Ford", "Focus", model_aliases=("Sport",)),
        VehicleCandidate("2", "Ford", "Fiesta", model_aliases=("Sport",)),
    ))
    assert index.recover_model_from_evidence("Ford", {"brand": "Ford Sport"}) is None


def test_unrelated_longest_evidence_cannot_be_discarded():
    index = ManufacturerCandidateIndex((*catalog(),
        VehicleCandidate("3", "VOLVO", "S60", model_aliases=("ABC",)),
    ))
    assert index.recover_model_from_evidence("VOLVO", {"brand": "V70 ABC"}) is None


def saab_catalog():
    return (
        VehicleCandidate("3", "SAAB", "9-3 (YS3D)", model_aliases=("9-3",), year_from=1998, year_to=2003),
        VehicleCandidate("4", "SAAB", "9-3 (YS3F)", model_aliases=("9-3",), year_from=2002, year_to=2015),
        VehicleCandidate("5", "SAAB", "9-5 (YS3E)", model_aliases=("9-5",), year_from=1997, year_to=2009),
        VehicleCandidate("6", "SAAB", "9-5 (YS3G)", model_aliases=("9-5",), year_from=2010, year_to=2012),
    )


@pytest.mark.parametrize("name,label", [("9-3", "9 3"), ("9-5", "9 5"), ("9‑3", "9 3"), ("9 - 5", "9 5")])
@pytest.mark.parametrize("field", ["brand", "model"])
def test_hyphenated_saab_name_recovers_catalog_family(name, label, field):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence(
        "Saab", {field: f"SAAB {name} LINEAR SPORTCOM"}
    ) == (label, field)


@pytest.mark.parametrize("text", ["19-3", "9-30", "9-3X", "9/3", "9.3", "9 3", "93", "95", "A9-3", "9-3A"])
def test_numeric_fragments_do_not_recover_saab(text):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence("SAAB", {"brand": text}) is None


@pytest.mark.parametrize("field", ["eeg_type_approval", "variant", "version", "model_no", "type_text"])
def test_saab_short_names_cannot_come_from_identifier_fields(field):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence("SAAB", {field: "9-3"}) is None


def test_saab_field_provenance_does_not_prefer_ineligible_approval():
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence(
        "SAAB", {"brand": "SAAB 9-3", "eeg_type_approval": "9/3"}
    ) == ("9 3", "brand")


def test_saab_scope_requires_catalog_and_family_prefix_not_trim_alias():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Ford", "9-3", model_aliases=("9-3",)),
        VehicleCandidate("2", "SAAB", "900", model_aliases=("9-3",)),
    ))
    assert index.recover_model_from_evidence("Ford", {"brand": "9-3"}) is None
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3"}) is None
    assert ManufacturerCandidateIndex(()).recover_model_from_evidence("SAAB", {"brand": "9-5"}) is None


def test_saab_contradictory_names_and_unrecognized_explicit_model_fail_closed():
    index = ManufacturerCandidateIndex(saab_catalog())
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3 9-5"}) is None
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3", "model": "UNKNOWN"}) is None


def test_saab_recovery_preserves_ambiguity_and_technical_gates():
    evaluator = TecDocDryRunEvaluator(saab_catalog())
    payload = {"normalized": {"manufacturer": "SAAB"}, "source_evidence": {"brand": "SAAB 9-3"}}
    ambiguous = evaluator.evaluate(MatchSourceRecord(1, payload))
    assert ambiguous.terminal == "review_required"
    assert "route:candidate_margin_below_gate" in ambiguous.reason_codes
    assert "model_evidence_missing" not in ambiguous.reason_codes
    dated = evaluator.evaluate(MatchSourceRecord(2, {
        **payload, "normalized": {"manufacturer": "SAAB", "production_year": 2010},
    }))
    assert dated.terminal == "resolved"
    assert dated.top_candidate_reference == "4"
    conflicting = evaluator.evaluate(MatchSourceRecord(3, {
        **payload, "normalized": {"manufacturer": "SAAB", "production_year": 1980},
    }))
    assert conflicting.terminal != "resolved"
    assert "conflict:year" in conflicting.reason_codes
