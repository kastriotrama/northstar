import pytest

from scripts.review_independent_approval_evidence import approval_query, join_golf_evidence


def sample():
    target = {"row_key": "private-hash", "raw_source_evidence": {
        "eeg_type_approval": "e1*x*01", "variant": "V", "version": "ONE", "type_text": "AU", "body_code": "AC"}}
    name = {"typegoedkeuringsnummer": "e1*x*01", "codevariantgk": "V", "codeuitvoeringtgk": "ONE",
            "volgnummerrevisieuitvoering": "0", "typeaanduidingfabrikant": "AU", "handelsbenamingfabrikant": "Golf"}
    body = {"typegoedkeuringsnummer": "e1*x*01", "codevarianttgk": "V", "codeuitvoeringtgk": "ONE",
            "volgnummerrevisieuitvoering": "0", "codecarrosserietype": "AB"}
    return target, name, body


def test_exact_join_exposes_body_disagreement_without_approving_mapping():
    target, name, body = sample()
    row = join_golf_evidence([target], [name], [body])[0]
    assert row["status"] == "exact_source_evidence"
    assert row["source_body_disagreement"] is True
    assert row["approved_model_rule"] is False


@pytest.mark.parametrize("field", ["typegoedkeuringsnummer", "codevariantgk", "codeuitvoeringtgk"])
def test_no_partial_approval_variant_or_version_join(field):
    target, name, body = sample()
    name[field] += "OTHER"
    assert join_golf_evidence([target], [name], [body])[0]["status"] == "missing_exact_approval_variant_version"


def test_revision_and_type_must_agree_and_ambiguity_is_retained():
    target, name, body = sample()
    body["volgnummerrevisieuitvoering"] = "1"
    result = join_golf_evidence([target], [name], [body])[0]
    assert result["rdw_body_codes"] == []
    assert result["status"] == "incomplete_or_ambiguous_source_evidence"
    body["volgnummerrevisieuitvoering"] = "0"
    conflicting = {**name, "typeaanduidingfabrikant": "AUV", "handelsbenamingfabrikant": "Golf Variant"}
    result = join_golf_evidence([target], [name, conflicting], [body])[0]
    assert result["status"] == "incomplete_or_ambiguous_source_evidence"
    assert result["rdw_names"] == ["Golf", "Golf Variant"]


def test_query_preserves_literal_stars_and_rejects_injection():
    assert approval_query(["e1*x*01"]) == "typegoedkeuringsnummer in ('e1*x*01')"
    with pytest.raises(ValueError):
        approval_query(["x' OR 1=1"])
