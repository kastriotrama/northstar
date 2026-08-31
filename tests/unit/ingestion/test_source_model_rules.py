import hashlib
import json
from dataclasses import asdict, replace

import pytest

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.tecdoc.source_model_rules import (
    ReviewedSourceModelPolicy,
    ReviewedSourceModelRule,
    reviewed_source_model_policy,
)


def rule(**kwargs):
    return replace(ReviewedSourceModelRule(
        "TEST-SOURCE-MODEL", "VW", "GOLF", "GOLF VII Variant",
        (("eeg_type_approval", "e1*test*01"), ("type_text", "AUV"), ("body_code", "AC")),
        "synthetic-reviewer", "synthetic-independent-source",
    ), **kwargs)


def catalog(*, candidate_only=False):
    common = {"manufacturer": "VW", "year_from": 2015, "year_to": 2020,
              "fuels": frozenset({"petrol"}), "power_kw": 110, "displacement_cc": 1498}
    return (
        VehicleCandidate("1", model="GOLF VII", model_aliases=("GOLF",), bodyworks=frozenset({"hatchback"}), **common),
        VehicleCandidate("2", model="GOLF VII Variant", model_aliases=("GOLF VARIANT",), bodyworks=frozenset({"estate"}),
                         candidate_type="TecDocKTypeCandidateOnly" if candidate_only else "TecDocKType", **common),
    )


def record(**source_changes):
    return MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VW", "model_family": "GOLF", "production_year": 2019,
                       "fuel_match_tokens": ["petrol"], "power_kw": 110, "displacement_cc": 1498, "bodywork_form": "estate"},
        "source_evidence": {"brand": "VW", "model": "GOLF", "body_code": "AC", "type_text": "AUV",
                            "eeg_type_approval": "e1*test*01", **source_changes},
    })


def evaluator(candidates=None, **kwargs):
    return TecDocDryRunEvaluator(candidates or catalog(),
                                source_model_policy=ReviewedSourceModelPolicy("synthetic-v1", (rule(),)), **kwargs)


def test_disabled_by_default_and_synthetic_reviewed_family_retains_technical_gates():
    assert TecDocDryRunEvaluator(catalog()).evaluate(record()).terminal == "review_required"
    resolved = evaluator().evaluate(record())
    assert resolved.terminal == "resolved" and resolved.top_candidate_reference == "2"
    assert "source_model_rule:TEST-SOURCE-MODEL" in resolved.reason_codes
    assert any(r.startswith("source_model_policy:") for r in resolved.reason_codes)
    wrong_power = record()
    wrong_power.payload["normalized"]["power_kw"] = 220
    assert evaluator().evaluate(wrong_power).terminal != "resolved"


@pytest.mark.parametrize("changes", [{"body_code": "AB"}, {"type_text": "AU"},
                                     {"eeg_type_approval": "e1*test*02"}, {"eeg_type_approval": "e1*test*"},
                                     {"model": "GOLF PLUS"}])
def test_exact_source_scope_and_cache_do_not_leak(changes):
    engine = evaluator()
    assert engine.evaluate(record()).terminal == "resolved"
    result = engine.evaluate(record(**changes))
    assert not any(reason.startswith("source_model_rule:") for reason in result.reason_codes)


def test_ambiguous_ktype_and_candidate_only_are_not_promoted_by_a_family_rule():
    assert evaluator(catalog(candidate_only=True)).evaluate(record()).terminal == "provisional"
    twins = (*catalog(), replace(catalog()[1], candidate_reference="3"))
    result = evaluator(twins).evaluate(record())
    assert result.terminal == "review_required"
    assert "route:candidate_margin_below_gate" in result.reason_codes


def test_opposing_reviewed_targets_and_source_contradictions_fail_closed():
    policy = ReviewedSourceModelPolicy("synthetic-v2", (rule(), rule(rule_id="other", target_model="GOLF VII")))
    result = TecDocDryRunEvaluator(catalog(), source_model_policy=policy).evaluate(record())
    assert result.reason_codes == ("source_model_rules_conflict",)
    assert evaluator().evaluate(record(brand="VW GOLF VARIANT")).terminal == "review_required"


@pytest.mark.parametrize("changes", [{"reviewed_by": ""}, {"evidence_ref": ""},
                                     {"source_conditions": (("body_code", "AC"),)},
                                     {"source_conditions": (("plate", "ABC123"),)},
                                     {"source_conditions": (("eeg_type_approval", "x"), ("type_text", "A"), ("type_text", "B"))}])
def test_rule_requires_independent_scope_and_review_evidence(changes):
    with pytest.raises(ValueError):
        rule(**changes)


def test_target_must_be_canonical_catalog_family_not_a_trim_alias_or_other_manufacturer():
    for changes in ({"target_model": "GOLF"}, {"manufacturer": "Ford"}):
        with pytest.raises(ValueError, match="canonical family"):
            TecDocDryRunEvaluator(catalog(), source_model_policy=ReviewedSourceModelPolicy("test", (rule(**changes),)))


def test_manifest_is_pinned_approved_and_not_implicitly_activated():
    definition = asdict(rule())
    definition["source_conditions"] = dict(rule().source_conditions)
    payload = {"version": "test-v1", "status": "approved", "rules": [{**definition, "status": "approved"}]}
    def load(**kwargs):
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return reviewed_source_model_policy(payload, expected_version=kwargs.get("version", "test-v1"),
                                            expected_digest=kwargs.get("checksum", checksum))
    assert len(load().rules) == 1
    with pytest.raises(ValueError, match="checksum"):
        load(checksum="wrong")
    with pytest.raises(ValueError, match="unapproved"):
        load(version="wrong")
    payload["rules"][0]["status"] = "proposed"
    with pytest.raises(ValueError, match="unapproved"):
        load()
    payload["status"] = "proposed"
    with pytest.raises(ValueError, match="unapproved"):
        load()
