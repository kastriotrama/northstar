from ingestion.confidence_routing import ConfidenceRoutingDecision
from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.model_aliases import (
    ReviewedModelAliasIndex,
    prefer_non_degrading_alias_decision,
)
from ingestion.translation_dictionaries import TranslationRule, TranslationRuleSet


def rule(
    rule_id: str,
    manufacturer: str,
    terms: tuple[str, ...],
    canonical: str,
    *,
    decision: str = "accepted",
) -> TranslationRule:
    return TranslationRule(
        rule_id=rule_id,
        area="model_family",
        source_fields=("model",),
        source_terms=terms,
        canonical_field="model_family",
        canonical_value=canonical,
        decision=decision,
        manufacturers=(manufacturer,),
    )


def test_expands_reviewed_aliases_within_manufacturer_scope() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(rule("MOD-001", "Volkswagen", ("T ROC", "T-ROC"), "T-Roc"),),
        )
    )
    candidate = VehicleCandidate(
        candidate_reference="1",
        manufacturer="VOLKSWAGEN",
        model="T-ROC (A11)",
        model_aliases=("1.5 TSI",),
    )

    expanded = index.expand(candidate)

    assert set(expanded.model_aliases) == {"1.5 TSI", "T ROC", "T-ROC", "T-Roc"}


def test_alias_does_not_cross_manufacturer_scope() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(rule("MOD-001", "Citroën", ("C4",), "C4"),),
        )
    )

    evidence = index.evidence_for(manufacturer="BMW", model_family="C4")

    assert evidence.aliases == ()
    assert evidence.rule_ids == ()


def test_proposed_rules_are_never_candidate_aliases() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(
                rule(
                    "MOD-001",
                    "BMW",
                    ("UNREVIEWED",),
                    "X1",
                    decision="proposed",
                ),
            ),
        )
    )

    assert index.evidence_for(manufacturer="BMW", model_family="X1").aliases == ()


def test_compact_numeric_prefix_does_not_match_larger_model_number() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(rule("MOD-001", "Polestar", ("2",), "2"),),
        )
    )

    assert index.evidence_for(manufacturer="Polestar", model_family="2008").aliases == ()


def test_reports_rule_provenance_for_audit() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(rule("MOD-001", "Volvo", ("XC 40",), "XC40"),),
        )
    )

    evidence = index.evidence_for(manufacturer="VOLVO", model_family="XC40 II")

    assert set(evidence.aliases) == {"XC 40", "XC40"}
    assert evidence.rule_ids == ("MOD-001",)


def decision(route: str, confidence: float) -> ConfidenceRoutingDecision:
    selected = None if route == "review_required" else "ktype:1"
    return ConfidenceRoutingDecision(
        policy_version="test-v1",
        route=route,
        confidence=confidence,
        selected_candidate_reference=selected,
        top_candidate_reference="ktype:1",
        reason_codes=(),
        hard_conflicts=(),
        decision_trace=(),
        alternative_candidates=(),
    )


def test_alias_decision_can_improve_but_not_downgrade_base_route() -> None:
    provisional = decision("provisional", 0.8)
    review = decision("review_required", 0.99)
    resolved = decision("resolved", 0.9)

    assert prefer_non_degrading_alias_decision(provisional, review) is provisional
    assert prefer_non_degrading_alias_decision(provisional, resolved) is resolved


def test_alias_decision_needs_higher_confidence_to_replace_equal_route() -> None:
    base = decision("resolved", 0.96)
    weaker_alias = decision("resolved", 0.95)
    stronger_alias = decision("resolved", 0.97)

    assert prefer_non_degrading_alias_decision(base, weaker_alias) is base
    assert prefer_non_degrading_alias_decision(base, stronger_alias) is stronger_alias


def test_a_numbered_series_covers_the_families_tecdoc_names_by_number() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(rule("MOD-197", "BMW", ("320D", "3"), "3 Series"),),
        )
    )

    for family in ("3 (F30, F80)", "3 Touring (F31)", "3 Gran Turismo (F34)", "3 Series"):
        assert index.evidence_for(manufacturer="BMW", model_family=family).rule_ids == ("MOD-197",)
    for family in ("X3 (G01)", "30", "M3 (E30)", "i3 (I01)"):
        assert index.evidence_for(manufacturer="BMW", model_family=family).aliases == ()


def test_rules_scoped_to_the_ts_name_reach_the_catalogs_name_once_rescoped() -> None:
    index = ReviewedModelAliasIndex(
        TranslationRuleSet(
            version="rules-v1",
            rules=(
                rule("MOD-001", "Volkswagen", ("PASSAT",), "Passat"),
                rule("MOD-900", "Unknown Make", ("ONE",), "One"),
            ),
        )
    )
    catalog_name = {"VOLKSWAGEN": "VW"}

    scoped = index.scoped_to_catalog(catalog_name.get)

    family = "PASSAT B8 Variant (3G5, CB5)"
    assert index.evidence_for(manufacturer="VW", model_family=family).aliases == ()
    assert scoped.evidence_for(manufacturer="VW", model_family=family).rule_ids == ("MOD-001",)
    # The TS name keeps working; a name the catalog cannot resolve keeps only its own entry.
    assert scoped.evidence_for(manufacturer="Volkswagen", model_family=family).rule_ids == ("MOD-001",)
    assert scoped.evidence_for(manufacturer="Unknown Make", model_family="One").rule_ids == ("MOD-900",)
