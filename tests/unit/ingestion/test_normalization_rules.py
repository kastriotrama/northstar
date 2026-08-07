from dataclasses import replace

from ingestion.normalization_rules import manufacturer_entity_catalog, normalize_ts_record
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    TranslationRuleSet,
    load_translation_rule_set,
)


def test_accepted_values_are_normalized_without_identifiers() -> None:
    outcome = normalize_ts_record(
        {
            "plate": "ABC123",
            "vin": "SENSITIVEVIN123456",
            "manufacturer": "Volvo Car Corporation",
            "model": "V60",
            "model_year": 2024,
            "eu_category": "M1",
            "body_code": "AC",
            "gearbox": "Z",
        }
    )

    assert outcome.status == "provisional"
    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.candidates["model_family"] == "V60"
    assert outcome.pipeline_version == "normalization-pipeline-v5"
    assert [entry.sequence for entry in outcome.decision_trace] == list(
        range(1, len(outcome.decision_trace) + 1)
    )
    traced_fields = {
        (entry.transformer_id, entry.field, entry.rule_ids) for entry in outcome.decision_trace
    }
    assert ("ts.manufacturer", "manufacturer", ("MFR-102",)) in traced_fields
    assert ("ts.bodywork", "bodywork_form", ("BDY-110",)) in traced_fields
    assert ("ts.transmission", "transmission_type", ("TRN-008",)) in traced_fields
    manufacturer_trace = next(
        entry
        for entry in outcome.decision_trace
        if entry.transformer_id == "ts.manufacturer" and entry.field == "manufacturer"
    )
    assert manufacturer_trace.before == "Volvo Car Corporation"
    assert manufacturer_trace.after == "Volvo"
    assert "ABC123" not in str(outcome.to_payload())
    assert "SENSITIVEVIN123456" not in str(outcome.to_payload())


def test_registry_bodywork_confirmation_allows_out_of_scope_marketing_term() -> None:
    outcome = normalize_ts_record(
        {
            "brand": "NIO",
            "model": "ET5 TOURING",
            "eu_category": "M1",
            "body_code": "AC",
        },
        manufacturer_entity_rules={
            "brand:NIO": {
                "entity_id": "MFE-NIO",
                "canonical_name": "NIO",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
            }
        },
    )

    assert outcome.normalized["manufacturer"] == "NIO"
    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["marketing_body_style"] == "estate"
    assert "bodywork_marketing_scope_unresolved" not in outcome.review_reasons


def test_approved_passenger_body_code_98_normalizes_as_other() -> None:
    base = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    rules = tuple(
        replace(rule, canonical_value="other") if rule.rule_id == "BDY-120" else rule
        for rule in base.rules
    )
    outcome = normalize_ts_record(
        {"brand": "VOLVO", "eu_category": "M1", "body_code": "98"},
        rule_set=TranslationRuleSet(version="test-body-98", rules=rules),
    )

    assert outcome.normalized["bodywork_form"] == "other"
    assert outcome.normalized["bodywork_registry_code"] == "98"
    assert "bodywork_requires_review" not in outcome.review_reasons


def test_converter_uses_recognized_base_manufacturer_and_is_retained() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Brabus GmbH", "base_manufacturer": "Mercedes-Benz AG"}
    )
    assert outcome.normalized["manufacturer"] == "Mercedes-Benz"
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"
    assert outcome.normalized["builder_converter_names"] == ["Brabus"]
    assert outcome.normalized["legal_manufacturer"] == "Brabus GmbH"
    assert outcome.normalized["base_manufacturer"] == "Mercedes-Benz AG"


def test_unknown_converter_uses_optional_base_only_when_brand_confirms_oem() -> None:
    outcome = normalize_ts_record(
        {
            "brand": "VOLKSWAGEN",
            "model": "CRAFTER",
            "manufacturer": "NORDIC VEHICLE CONVERSION AB",
            "base_manufacturer": "VOLKSWAGEN AG",
            "fab_code": "VW",
        }
    )

    assert outcome.normalized["manufacturer"] == "Volkswagen"
    assert outcome.normalized["legal_manufacturer"] == "NORDIC VEHICLE CONVERSION AB"
    assert outcome.normalized["base_manufacturer"] == "VOLKSWAGEN AG"
    assert outcome.normalized["builder_converter_names"] == ["NORDIC VEHICLE CONVERSION AB"]
    assert outcome.normalized["manufacturer_evidence"] == ["brand", "base_manufacturer"]
    assert "manufacturer_unknown" not in outcome.review_reasons


def test_short_manufacturer_and_base_fragments_repair_only_with_brand_confirmation() -> None:
    repaired = normalize_ts_record(
        {
            "brand": "NISSAN",
            "model": "NISSAN LEAF 40KWH",
            "manufacturer": "NI",
            "base_manufacturer": "SSAN",
            "fab_code": "NA",
        }
    )
    unsafe = normalize_ts_record(
        {
            "brand": "VOLKSWAGEN",
            "manufacturer": "NI",
            "base_manufacturer": "SSAN",
        }
    )

    assert repaired.normalized["manufacturer"] == "Nissan"
    assert repaired.normalized["legal_manufacturer"] == "Nissan"
    assert "base_manufacturer" not in repaired.normalized
    assert repaired.normalized["manufacturer_source_repair"] == (
        "concatenated_manufacturer_base_fragments"
    )
    assert "MFR-FRAGMENTED-SOURCE-REPAIR" in repaired.applied_rule_ids
    assert "manufacturer" not in unsafe.normalized
    assert "manufacturer_unknown" in unsafe.review_reasons


def test_kabe_converter_uses_fiat_base_and_retains_builder() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "KABE AB BOX 14 561 06 TENHULT",
            "base_manufacturer": "FCA ITALY S.P.A. C. SO G. AGNELLI 200",
        }
    )

    assert outcome.normalized["manufacturer"] == "Fiat"
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"
    assert outcome.normalized["builder_converter_names"] == ["KABE"]


def test_kia_legal_entity_is_a_recognized_manufacturer() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "KIAMOTORSCORPORATION",
            "brand": "KIA",
            "model": "NIRO",
            "vin": "KNA00000000000000",
        }
    )

    assert outcome.normalized["manufacturer"] == "Kia"
    assert "manufacturer_unknown" not in outcome.review_reasons


def test_mini_requires_agreeing_parent_brand_model_and_vin_evidence() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "BAYERISCHE MOTOREN WERKE AG, DE-80788 MÜNCHEN",
            "brand": "MINI",
            "model": "COOPER",
            "vin": "WMW00000000000000",
        }
    )

    assert outcome.normalized["manufacturer"] == "MINI"
    assert {"MFR-PARENT-MARKETED", "MFR-BRAND-MODEL", "MFR-BRAND-VIN-WMI"} <= set(
        outcome.applied_rule_ids
    )


def test_multibrand_groups_require_marketed_brand_evidence() -> None:
    peugeot = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "PEUGEOT", "model": "2008"}
    )
    jeep = normalize_ts_record(
        {"manufacturer": "FCA ITALY S.P.A.", "brand": "JEEP", "model": "COMPASS"}
    )
    unresolved = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "PEUGEOT", "model": "UNKNOWN"}
    )

    assert peugeot.normalized["manufacturer"] == "Peugeot"
    assert jeep.normalized["manufacturer"] == "Jeep"
    assert "manufacturer" not in unresolved.normalized
    assert "manufacturer_corporate_group_unresolved" in unresolved.review_reasons


def test_parent_company_rows_use_agreeing_consumer_brand_and_model() -> None:
    ds = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "DS", "model": "DS 7 CROSSBACK"}
    )
    lexus = normalize_ts_record(
        {
            "manufacturer": "TOYOTA MOTOR EUROPE NV/SA",
            "base_manufacturer": "TOYOTA MOTOR EUROPE NV/SA",
            "brand": "LEXUS",
            "model": "LEXUS RX450H",
        }
    )

    assert ds.normalized["manufacturer"] == "DS"
    assert lexus.normalized["manufacturer"] == "Lexus"
    assert "manufacturer_evidence_conflict" not in lexus.review_reasons


def test_ds4_requires_ds_brand_and_psa_parent_context() -> None:
    ds = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "DS", "model": "DS4"}
    )
    citroen = normalize_ts_record(
        {
            "brand": "CITROEN N",
            "model": "DS4",
            "fab_code": "CI",
            "vin": "VF700000000000000",
        }
    )

    assert ds.normalized["manufacturer"] == "DS"
    assert citroen.normalized["manufacturer"] == "Citroën"
    assert "manufacturer_evidence_conflict" not in citroen.review_reasons


def test_ds_brand_fab_and_model_override_shared_citroen_wmi() -> None:
    outcome = normalize_ts_record(
        {
            "brand": "DS",
            "model": "DS 3",
            "fab_code": "DSS",
            "vin": "VF700000000000000",
        }
    )

    assert outcome.normalized["manufacturer"] == "DS"
    assert "manufacturer_evidence_conflict" not in outcome.review_reasons


def test_unknown_legal_parent_uses_reviewed_model_brand_child() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "GREAT WALL MOTOR COMPANY LIMITED",
            "brand": "GREAT WALL MOTOR COMPANY",
            "model": "ORA FUNKY CAT",
        },
        manufacturer_entity_rules={
            "manufacturer:GREAT WALL MOTOR COMPANY LIMITED": {
                "entity_id": "MFE-GREAT-WALL",
                "canonical_name": None,
                "entity_role": "unknown",
                "base_behavior": "require_evidence_review",
                "source_term": "GREAT WALL MOTOR COMPANY LIMITED",
            }
        },
    )

    assert outcome.normalized["manufacturer"] == "ORA"
    assert "MFR-PARENT-CHILD-EVIDENCE" in outcome.applied_rule_ids
    assert "manufacturer_entity_requires_review" not in outcome.review_reasons


def test_great_wall_legal_parent_uses_ora_model_brand_without_entity_override() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "GREAT WALL MOTOR COMPANY LIMITED",
            "brand": "GREAT WALL MOTOR COMPANY",
            "model": "ORA FUNKY CAT",
        }
    )

    assert outcome.normalized["manufacturer"] == "ORA"
    assert "MFR-PARENT-MODEL-CHILD" in outcome.applied_rule_ids
    assert "manufacturer_unknown" not in outcome.review_reasons


def test_self_built_vehicle_is_valid_without_a_manufacturer_marque() -> None:
    outcome = normalize_ts_record({"brand": "EGEN TILLVERKNING", "body_code": "02"})

    assert "manufacturer" not in outcome.normalized
    assert outcome.normalized["manufacturer_role"] == "self_built"
    assert "manufacturer_missing" not in outcome.review_reasons


def test_model_candidate_is_extracted_from_composite_or_duplicated_brand_text() -> None:
    composite = normalize_ts_record(
        {"brand": "BMW523I"},
        manufacturer_entity_rules={
            "brand:BMW523I": {
                "entity_id": "MFE-BMW523I",
                "canonical_name": "BMW",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
            }
        },
    )
    duplicated = normalize_ts_record(
        {"brand": "MITSUBISHI", "manufacturer": "MITSUBISHI", "model": "MITSUBISHI SPACE STAR"}
    )

    assert composite.candidates["model_family"] == "523I"
    assert duplicated.candidates["model_family"] == "SPACE STAR"


def test_missing_manufacturer_accepts_brand_only_with_corroborating_model() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "model": "V70"})

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert "manufacturer_missing_compare_brand" not in outcome.review_reasons


def test_missing_manufacturer_accepts_brand_with_matching_ktype() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "ktype_manufacturer": "Volvo"})

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert "MFR-BRAND-KTYPE" in outcome.applied_rule_ids


def test_reviewed_exact_brand_entity_resolves_missing_tillverkare() -> None:
    outcome = normalize_ts_record(
        {"brand": "AUDI A4 2.0TS QUATTRO"},
        manufacturer_entity_rules={
            "brand:AUDI A4 2 0TS QUATTRO": {
                "entity_id": "MFE-AUDI-A4",
                "canonical_name": "Audi",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
            }
        },
    )

    assert outcome.normalized["manufacturer"] == "Audi"
    assert outcome.normalized["manufacturer_role"] == "vehicle_manufacturer"
    assert "MFE-AUDI-A4" in outcome.applied_rule_ids
    assert "manufacturer_missing" not in outcome.review_reasons


def test_all_reviewed_legacy_brand_entities_resolve_without_tillverkare() -> None:
    legacy_entities = [
        item for item in manufacturer_entity_catalog() if item["source_field"] == "brand"
    ]

    assert len(legacy_entities) == 103
    for entity in legacy_entities:
        outcome = normalize_ts_record({"brand": entity["source_term"]})
        assert outcome.normalized["manufacturer"] == entity["canonical_name"]
        assert outcome.normalized["manufacturer_role"] == "vehicle_manufacturer"
        assert "manufacturer_missing" not in outcome.review_reasons


def test_reviewed_converter_entity_uses_base_manufacturer_and_retains_builder() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "CUSTOM CAMPER AB", "base_manufacturer": "FIAT AUTO SPA"},
        manufacturer_entity_rules={
            "manufacturer:CUSTOM CAMPER AB": {
                "entity_id": "MFE-CUSTOM-CAMPER",
                "canonical_name": "Custom Camper",
                "entity_role": "bodybuilder_converter",
                "base_behavior": "use_base_manufacturer",
            }
        },
    )

    assert outcome.normalized["manufacturer"] == "Fiat"
    assert outcome.normalized["builder_converter_names"] == ["Custom Camper"]
    assert outcome.normalized["manufacturer_role"] == "bodybuilder_converter"


def test_conflicting_ktype_prevents_brand_model_confirmation() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "model": "V70", "ktype_manufacturer": "BMW"})

    assert outcome.status == "review_required"
    assert "manufacturer" not in outcome.normalized
    assert outcome.candidates["manufacturer"] == "Volvo"
    assert "manufacturer_evidence_conflict" in outcome.review_reasons


def test_unknown_manufacturer_never_falls_back_to_populated_base() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Unknown Coach Company", "base_manufacturer": "Volvo"}
    )
    assert outcome.status == "review_required"
    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_unknown" in outcome.review_reasons


def test_unknown_manufacturer_is_not_replaced_by_agreeing_brand_and_model() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Unknown Coach Company",
            "brand": "Volvo",
            "model": "V70",
            "base_manufacturer": "BMW",
        }
    )

    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_evidence_conflict" in outcome.review_reasons


def test_recognized_brand_is_only_a_candidate_when_manufacturer_is_missing() -> None:
    outcome = normalize_ts_record({"brand": "Volvo", "base_manufacturer": "BMW"})

    assert outcome.status == "review_required"
    assert outcome.candidates["manufacturer"] == "Volvo"
    assert "manufacturer" not in outcome.normalized
    assert "manufacturer_evidence_conflict" in outcome.review_reasons


def test_approved_policy_uses_manufacturer_entity_alias_from_model_when_brand_missing() -> None:
    outcome = normalize_ts_record(
        {"model": "VOLVO V70"},
        manufacturer_entity_rules={
            "policy:MFR-MODEL-VARIANT-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-MODEL-VARIANT-FALLBACK",
                "allowed_fields": ["model", "variant"],
                "match_type": "whole_token_prefix",
            }
        },
    )

    assert outcome.status == "provisional"
    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.candidates["manufacturer_confirmation"] == {
        "canonical_name": "Volvo",
        "source_fields": ["model"],
    }
    assert "MFR-MODEL-VARIANT-FALLBACK" in outcome.candidate_rule_ids
    assert "manufacturer_missing" not in outcome.review_reasons


def test_model_variant_policy_does_not_use_substrings_or_override_populated_brand() -> None:
    rules = {
        "policy:MFR-MODEL-VARIANT-FALLBACK": {
            "kind": "manufacturer_match_policy",
            "rule_id": "MFR-MODEL-VARIANT-FALLBACK",
            "allowed_fields": ["model", "variant"],
            "match_type": "whole_token_prefix",
        }
    }

    substring = normalize_ts_record({"model": "OXFORD SPECIAL"}, manufacturer_entity_rules=rules)
    populated_brand = normalize_ts_record(
        {"brand": "UNREVIEWED", "model": "VOLVO V70"},
        manufacturer_entity_rules=rules,
    )

    assert "manufacturer" not in substring.normalized
    assert "manufacturer_missing" in substring.review_reasons
    assert "manufacturer" not in populated_brand.normalized
    assert "manufacturer_missing" in populated_brand.review_reasons


def test_model_variant_policy_routes_conflicting_manufacturer_aliases_to_review() -> None:
    outcome = normalize_ts_record(
        {"model": "VOLVO V70", "variant": "BMW 320D"},
        manufacturer_entity_rules={
            "policy:MFR-MODEL-VARIANT-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-MODEL-VARIANT-FALLBACK",
                "allowed_fields": ["model", "variant"],
                "match_type": "whole_token_prefix",
            }
        },
    )

    assert outcome.status == "review_required"
    assert outcome.candidates["manufacturer"] == ["BMW", "Volvo"]
    assert "manufacturer_model_variant_conflict" in outcome.review_reasons


def test_approved_brand_prefix_policy_uses_reviewed_manufacturer_alias() -> None:
    outcome = normalize_ts_record(
        {"brand": "VOLVO 945-811 SE 2.3"},
        manufacturer_entity_rules={
            "policy:MFR-BRAND-PREFIX-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
                "match_type": "whole_token_prefix",
                "review_terms": ["ADRIA", "DETHLEFFS", "DAIMLER"],
            }
        },
    )

    assert outcome.status == "provisional"
    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.candidates["manufacturer_confirmation"] == {
        "canonical_name": "Volvo",
        "source_fields": ["brand"],
    }
    assert "MFR-BRAND-PREFIX-FALLBACK" in outcome.candidate_rule_ids


def test_brand_prefix_policy_uses_approved_database_entity_alias() -> None:
    outcome = normalize_ts_record(
        {"brand": "SAAB 9-5 VECTOR"},
        manufacturer_entity_rules={
            "policy:MFR-BRAND-PREFIX-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
                "match_type": "whole_token_prefix",
            },
            "brand:SAAB": {
                "kind": "manufacturer_entity",
                "entity_id": "MFE-BRAND-SAAB",
                "source_field": "brand",
                "source_term": "SAAB",
                "canonical_name": "Saab",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
                "match_type": "whole_token_prefix",
            },
        },
    )

    assert outcome.status == "provisional"
    assert outcome.normalized["manufacturer"] == "Saab"
    assert "MFE-BRAND-SAAB" in outcome.candidate_rule_ids


def test_brand_prefix_policy_keeps_compound_builder_and_marque_cases_in_review() -> None:
    rules = {
        "policy:MFR-BRAND-PREFIX-FALLBACK": {
            "kind": "manufacturer_match_policy",
            "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
            "match_type": "whole_token_prefix",
            "review_terms": ["ADRIA", "DETHLEFFS", "DAIMLER"],
        }
    }

    fiat = normalize_ts_record({"brand": "FIAT ADRIA A"}, manufacturer_entity_rules=rules)
    jaguar = normalize_ts_record(
        {"brand": "JAGUAR DAIMLER SOVEREIGN"},
        manufacturer_entity_rules={
            **rules,
            "brand:JAGUAR": {
                "kind": "manufacturer_entity",
                "entity_id": "MFE-BRAND-JAGUAR",
                "source_field": "brand",
                "source_term": "JAGUAR",
                "canonical_name": "Jaguar",
                "entity_role": "vehicle_manufacturer",
                "base_behavior": "use_entity",
                "match_type": "whole_token_prefix",
            },
        },
    )

    assert "manufacturer" not in fiat.normalized
    assert "manufacturer_brand_compound_review" in fiat.review_reasons
    assert "manufacturer" not in jaguar.normalized
    assert "manufacturer_brand_compound_review" in jaguar.review_reasons


def test_brand_prefix_policy_does_not_replace_stronger_confirmed_brand_evidence() -> None:
    outcome = normalize_ts_record(
        {"brand": "VOLVO", "model": "V70"},
        manufacturer_entity_rules={
            "policy:MFR-BRAND-PREFIX-FALLBACK": {
                "kind": "manufacturer_match_policy",
                "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
                "match_type": "whole_token_prefix",
            }
        },
    )

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert "MFR-BRAND-CONFIRMED" in outcome.applied_rule_ids
    assert "manufacturer_confirmation" not in outcome.candidates


def test_reviewed_brand_example_resolves_through_parent_entity_hierarchy() -> None:
    rules = {
        "policy:MFR-BRAND-PREFIX-FALLBACK": {
            "kind": "manufacturer_match_policy",
            "rule_id": "MFR-BRAND-PREFIX-FALLBACK",
            "match_type": "whole_token_prefix",
        },
        "brand:SAAB": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-BRAND-SAAB",
            "source_field": "brand",
            "source_term": "SAAB",
            "canonical_name": "Saab",
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
            "match_type": "whole_token_prefix",
            "reviewed_examples": ["SAAB SPORT", "SAAB V 4"],
        },
    }

    reviewed = normalize_ts_record({"brand": "SAAB SPORT"}, manufacturer_entity_rules=rules)
    unseen = normalize_ts_record({"brand": "SAAB NEW MODEL"}, manufacturer_entity_rules=rules)

    assert reviewed.status == "resolved"
    assert reviewed.normalized["manufacturer"] == "Saab"
    assert "MFE-BRAND-SAAB" in reviewed.applied_rule_ids
    assert "MFR-BRAND-REVIEWED-EXAMPLE" in reviewed.applied_rule_ids
    assert "MFR-BRAND-LEGACY-EXACT" not in reviewed.applied_rule_ids
    assert unseen.status == "provisional"
    assert unseen.normalized["manufacturer"] == "Saab"
    assert "MFR-BRAND-PREFIX-FALLBACK" in unseen.candidate_rule_ids


def test_general_manufacturer_rules_tolerate_diacritics_and_retain_converters() -> None:
    rules = {
        "manufacturer:SKODA AUTO": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-SKODA-AUTO",
            "source_field": "manufacturer",
            "source_term": "SKODA AUTO",
            "canonical_name": "Škoda",
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
            "match_type": "diacritic_insensitive_prefix",
        },
        "manufacturer:PSA AUTOMOBILES": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-PSA-AUTOMOBILES",
            "source_field": "manufacturer",
            "source_term": "PSA AUTOMOBILES",
            "aliases": ["P.S.A. AUTOMOBILES"],
            "canonical_name": "PSA Automobiles",
            "entity_role": "corporate_group",
            "base_behavior": "require_evidence_review",
            "match_type": "diacritic_insensitive_prefix",
            "marketed_brand_overrides": {"CITROËN": "Citroën"},
        },
        "manufacturer:KNAUS TABBERT": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-KNAUS-TABBERT",
            "source_field": "manufacturer",
            "source_term": "KNAUS TABBERT",
            "canonical_name": "Knaus",
            "entity_role": "bodybuilder_converter",
            "base_behavior": "use_base_manufacturer",
            "match_type": "diacritic_insensitive_prefix",
        },
        "brand:FIAT ADRIA": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-FIAT-ADRIA",
            "source_field": "brand",
            "source_term": "FIAT ADRIA",
            "canonical_name": "Adria",
            "entity_role": "bodybuilder_converter",
            "base_behavior": "use_base_manufacturer",
            "fallback_manufacturer": "Fiat",
            "match_type": "diacritic_insensitive_prefix",
        },
        "brand:FIAT DETHLEFFS": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-FIAT-DETHLEFFS",
            "source_field": "brand",
            "source_term": "FIAT DETHLEFFS",
            "canonical_name": "Dethleffs",
            "entity_role": "bodybuilder_converter",
            "base_behavior": "use_base_manufacturer",
            "fallback_manufacturer": "Fiat",
            "match_type": "diacritic_insensitive_prefix",
        },
    }

    skoda = normalize_ts_record(
        {"manufacturer": "ŠKODA AUTO, a.s., Mladá Boleslav"},
        manufacturer_entity_rules=rules,
    )
    citroen = normalize_ts_record(
        {"manufacturer": "P.S.A. AUTOMOBILES S.A.", "brand": "CITROËN"},
        manufacturer_entity_rules=rules,
    )
    knaus = normalize_ts_record(
        {"manufacturer": "KNAUS-TABBERT GmbH", "base_manufacturer": "FCA ITALY S.P.A."},
        manufacturer_entity_rules=rules,
    )
    adria = normalize_ts_record({"brand": "FIAT-ADRIA A"}, manufacturer_entity_rules=rules)
    dethleffs = normalize_ts_record(
        {"brand": "FIAT DETHLEFFS T 6701"}, manufacturer_entity_rules=rules
    )

    assert skoda.normalized["manufacturer"] == "Škoda"
    assert citroen.normalized["manufacturer"] == "Citroën"
    assert "MFR-CORPORATE-BRAND-OVERRIDE" in citroen.applied_rule_ids
    assert knaus.normalized["manufacturer"] == "Fiat"
    assert knaus.normalized["builder_converter_names"] == ["Knaus"]
    assert adria.normalized["manufacturer"] == "Fiat"
    assert adria.normalized["builder_converter_names"] == ["Adria"]
    assert dethleffs.normalized["manufacturer"] == "Fiat"
    assert dethleffs.normalized["builder_converter_names"] == ["Dethleffs"]


def test_general_manufacturer_rules_keep_token_boundaries_and_child_allow_lists() -> None:
    rules = {
        "manufacturer:SKODA AUTO": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-SKODA-AUTO",
            "source_field": "manufacturer",
            "source_term": "SKODA AUTO",
            "canonical_name": "Škoda",
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
            "match_type": "diacritic_insensitive_prefix",
        },
        "manufacturer:PSA AUTOMOBILES": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-PSA-AUTOMOBILES",
            "source_field": "manufacturer",
            "source_term": "PSA AUTOMOBILES",
            "canonical_name": "PSA Automobiles",
            "entity_role": "corporate_group",
            "base_behavior": "require_evidence_review",
            "match_type": "diacritic_insensitive_prefix",
            "marketed_brand_overrides": {"CITROËN": "Citroën"},
        },
    }

    lookalike = normalize_ts_record(
        {"manufacturer": "SKODAX AUTO"}, manufacturer_entity_rules=rules
    )
    unapproved_child = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "UNKNOWN"},
        manufacturer_entity_rules=rules,
    )
    corroborated_existing_child = normalize_ts_record(
        {"manufacturer": "PSA AUTOMOBILES SA", "brand": "PEUGEOT", "model": "2008"},
        manufacturer_entity_rules=rules,
    )

    assert "manufacturer" not in lookalike.normalized
    assert "manufacturer_unknown" in lookalike.review_reasons
    assert "manufacturer" not in unapproved_child.normalized
    assert "manufacturer_corporate_group_unresolved" in unapproved_child.review_reasons
    assert corroborated_existing_child.normalized["manufacturer"] == "Peugeot"
    assert "MFR-PARENT-MARKETED" in corroborated_existing_child.applied_rule_ids


def test_approved_compact_brand_prefix_handles_joined_and_spaced_names_only_when_enabled() -> None:
    rules = {
        "brand:CHEVROLET": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-CHEVROLET-COMPACT",
            "source_field": "brand",
            "source_term": "CHEVROLET",
            "canonical_name": "Chevrolet",
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
            "match_type": "approved_compact_prefix",
        },
        "brand:MG": {
            "kind": "manufacturer_entity",
            "entity_id": "MFE-MG-COMPACT",
            "source_field": "brand",
            "source_term": "MG",
            "canonical_name": "MG",
            "entity_role": "vehicle_manufacturer",
            "base_behavior": "use_entity",
            "match_type": "approved_compact_prefix",
        },
    }

    joined = normalize_ts_record(
        {"brand": "CHEVROLETV8 BEL AIR CAB"}, manufacturer_entity_rules=rules
    )
    spaced = normalize_ts_record(
        {"brand": "M G B BMC 1800"}, manufacturer_entity_rules=rules
    )
    embedded = normalize_ts_record(
        {"brand": "STEFANS CHEVROLET ROADSTER"}, manufacturer_entity_rules=rules
    )

    assert joined.normalized["manufacturer"] == "Chevrolet"
    assert joined.applied_rule_ids == ("MFE-CHEVROLET-COMPACT",)
    assert spaced.normalized["manufacturer"] == "MG"
    assert embedded.review_reasons == ("manufacturer_missing",)


def test_bodywork_codes_are_vehicle_category_scoped() -> None:
    passenger = normalize_ts_record(
        {"manufacturer": "Volvo", "eu_category": "M1", "body_code": "AC"}
    )
    goods = normalize_ts_record({"manufacturer": "Volvo", "eu_category": "N1", "body_code": "AC"})
    assert passenger.normalized["bodywork_form"] == "estate"
    assert goods.status == "review_required"
    assert "bodywork_form" not in goods.normalized


def test_special_purpose_body_codes_are_not_forced_into_body_shape() -> None:
    fire = normalize_ts_record(
        {
            "brand": "OPEL ASTRA COMBI GLS 512",
            "manufacturer": "OPEL",
            "eu_category": "M1",
            "body_code": "95",
        }
    )
    ambulance = normalize_ts_record(
        {
            "brand": "NILSSON V70 AMBULANS",
            "manufacturer": "NILSSON",
            "base_manufacturer": "VOLVO",
            "eu_category": "M1",
            "body_code": "99",
        }
    )

    assert fire.normalized["special_purpose_type"] == "fire_rescue_vehicle"
    assert fire.normalized["bodywork_registry_code"] == "95"
    assert fire.normalized["marketing_body_style"] == "estate"
    assert fire.candidates["bodywork_form"] == "estate"
    assert "bodywork_code_unresolved_for_category" not in fire.review_reasons
    assert ambulance.normalized["special_purpose_type"] == "ambulance"
    assert ambulance.normalized["bodywork_registry_code"] == "99"
    assert "bodywork_code_unresolved_for_category" not in ambulance.review_reasons


def test_primary_police_code_is_special_purpose_not_unknown_bodywork() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "SAAB", "eu_category": "M1", "body_code": "93"}
    )

    assert outcome.normalized["special_purpose_type"] == "police"
    assert outcome.normalized["bodywork_registry_code"] == "93"
    assert "bodywork_form" not in outcome.normalized
    assert "bodywork_code_unresolved_for_category" not in outcome.review_reasons


def test_registry_convertible_blocks_california_motorhome_false_positive() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "FERRARI",
            "brand": "FERRARI F149",
            "model": "CALIFORNIA",
            "eu_category": "M1",
            "body_code": "AE",
        }
    )

    assert outcome.normalized["bodywork_form"] == "convertible"
    assert outcome.candidates.get("bodywork_form") != "motorhome"
    assert "motorhome_supporting_evidence_missing" not in outcome.review_reasons


def test_pdk_supports_structured_automatic_transmission() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "PORSCHE", "model": "CAYMAN PDK", "gearbox": "A"}
    )

    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.normalized["transmission_name"] == "PDK"
    assert "transmission_structured_marketing_conflict" not in outcome.review_reasons


def test_secondary_body_code_enriches_purpose_without_replacing_primary_bodywork() -> None:
    camper = normalize_ts_record(
        {
            "manufacturer": "VOLVO",
            "eu_category": "M1",
            "body_code": "AF",
            "body_code2": "SA",
        }
    )
    police = normalize_ts_record(
        {
            "manufacturer": "VOLKSWAGEN",
            "eu_category": "M1",
            "body_code": "AC",
            "body_code2": "93",
        }
    )
    taxi = normalize_ts_record(
        {
            "manufacturer": "VOLVO",
            "eu_category": "M1",
            "body_code": "AB",
            "body_code2": "06",
        }
    )

    assert camper.normalized["bodywork_form"] == "multi_purpose_vehicle"
    assert camper.normalized["special_purpose_type"] == "motor_caravan"
    assert police.normalized["bodywork_form"] == "estate"
    assert police.normalized["special_purpose_type"] == "police"
    assert taxi.normalized["bodywork_form"] == "hatchback"
    assert taxi.normalized["usage_type"] == "taxi"


def test_registry_and_marketing_bodywork_are_retained_as_separate_facts() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "MERCEDES-BENZ AG",
            "model": "GLE 350 DE 4MATIC COUPE",
            "eu_category": "M1G",
            "body_code": "AC",
        }
    )

    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["bodywork_source"] == "registry"
    assert outcome.normalized["marketing_body_style"] == "coupe"
    assert "bodywork_structured_marketing_conflict" not in outcome.review_reasons


def test_runtime_rule_set_applies_an_activated_override() -> None:
    base = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    rules = tuple(
        replace(rule, canonical_value="sedan") if rule.rule_id == "BDY-110" else rule
        for rule in base.rules
    )
    override = TranslationRuleSet(version="ts-review-test", rules=rules)

    outcome = normalize_ts_record(
        {"manufacturer": "Volvo", "eu_category": "M1", "body_code": "AC"},
        rule_set=override,
    )

    assert outcome.normalized["bodywork_form"] == "sedan"


def test_reviewed_fuel_is_accepted_while_drive_remains_a_candidate() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "fuel1": "01", "fuel2": "03", "is_4wd": "1"}
    )
    assert outcome.status == "provisional"
    assert outcome.normalized["energy_sources"] == ["petrol", "electricity"]
    assert outcome.candidates["drive_type"] == "awd"
    assert "drive_type" not in outcome.normalized
    assert {match.rule_id for match in outcome.rule_matches} >= {"FUEL-001", "FUEL-003"}


def test_explicit_hybrid_marker_adds_electricity_to_petrol_carrier() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "Audi", "fuel1": "01", "fuel2": "0", "fuel3": "0", "ev_config": "ELHYBRID"}
    )

    assert outcome.normalized["energy_sources"] == ["petrol", "electricity"]
    assert outcome.normalized["electrification_type"] == "hybrid"
    assert "electrification_fuel_evidence_conflict" not in outcome.review_reasons


def test_explicit_hybrid_marker_preserves_underlying_diesel() -> None:
    outcome = normalize_ts_record({"manufacturer": "Audi", "fuel1": "02", "ev_config": "ELHYBRID"})

    assert outcome.normalized["energy_sources"] == ["diesel", "electricity"]
    assert outcome.normalized["electrification_type"] == "hybrid"


def test_hybrid_without_combustion_carrier_still_routes_to_review() -> None:
    outcome = normalize_ts_record({"manufacturer": "Audi", "fuel1": "03", "ev_config": "ELHYBRID"})

    assert "electrification_type" not in outcome.normalized
    assert "electrification_fuel_evidence_conflict" in outcome.review_reasons


def test_malformed_source_values_route_to_review() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "build_date": "20241341", "gearbox": "?", "fuel1": "99"}
    )
    assert outcome.status == "review_required"
    assert set(outcome.review_reasons) >= {
        "build_date_malformed",
        "transmission_code_unknown",
        "fuel1_code_unknown",
    }


def test_non_object_raw_record_fails_without_raising() -> None:
    outcome = normalize_ts_record("not-an-object")
    assert outcome.status == "failed"
    assert outcome.review_reasons == ("raw_record_not_object",)
    assert outcome.decision_trace[0].transformer_id == "ts.input-contract"
    assert outcome.decision_trace[0].confidence_effect == -1.0


def test_text_canonicalization_runs_before_translation_rules() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "  Volvo\u00a0Car   Corporation  ",
            "model": " Ｖ６０\u2003Recharge ",
            "eu_category": "m1",
            "body_code": "ac",
            "gearbox": "z",
        }
    )

    assert outcome.normalized["manufacturer"] == "Volvo"
    assert outcome.normalized["bodywork_form"] == "estate"
    assert outcome.normalized["transmission_type"] == "automatic"
    assert outcome.candidates["model_family"] == "V60 Recharge"
    assert any(
        entry.transformer_id == "ts.text-canonicalization"
        and entry.target == "canonical"
        and entry.field == "model"
        and entry.before == " Ｖ６０\u2003Recharge "
        and entry.after == "V60 Recharge"
        for entry in outcome.decision_trace
    )


def test_dates_are_extracted_with_explicit_precision_and_original_evidence() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "registration_date": "20240131",
            "build_month": "202312",
        }
    )

    assert outcome.normalized["registration_date"] == "2024-01-31"
    assert outcome.normalized["production_date"] == "2023-12"
    assert outcome.normalized["production_year"] == 2023
    assert outcome.normalized["production_date_precision"] == "month"
    assert {"DATE-REGISTRATION-V1", "DATE-PRODUCTION-MONTH-V1"} <= set(outcome.applied_rule_ids)
    registration_trace = next(
        entry
        for entry in outcome.decision_trace
        if entry.transformer_id == "ts.dates" and entry.field == "registration_date"
    )
    assert registration_trace.before == {
        "registration_date": "20240131",
        "build_month": "202312",
    }
    assert registration_trace.after == "2024-01-31"


def test_explicit_open_and_closed_production_ranges_are_supported() -> None:
    closed = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2019-05", "production_to": "2024"}
    )
    open_ended = normalize_ts_record({"manufacturer": "Volvo", "production_from": "20230101"})
    mixed_precision = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2024-06", "production_to": "2024"}
    )

    assert closed.normalized["production_from"] == "2019-05"
    assert closed.normalized["production_year_from"] == 2019
    assert closed.normalized["production_to"] == "2024"
    assert closed.normalized["production_year_to"] == 2024
    assert open_ended.normalized["production_from"] == "2023-01-01"
    assert "production_to" not in open_ended.normalized
    assert mixed_precision.normalized["production_from"] == "2024-06"
    assert mixed_precision.normalized["production_to"] == "2024"


def test_malformed_or_reversed_date_ranges_are_not_partially_normalized() -> None:
    malformed = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2020", "production_to": "202413"}
    )
    reversed_range = normalize_ts_record(
        {"manufacturer": "Volvo", "production_from": "2024", "production_to": "2020"}
    )

    assert "production_from" not in malformed.normalized
    assert "production_to_malformed" in malformed.review_reasons
    assert "production_from" not in reversed_range.normalized
    assert "production_to" not in reversed_range.normalized
    assert "production_range_reversed" in reversed_range.review_reasons


def test_engine_power_and_displacement_are_structured_without_marketing_inference() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "engine_code": " b4204t ",
            "engine_family_code": " vea ",
            "engine_family_name": " Volvo Engine Architecture ",
            "kw": 145,
            "ccm": 1969,
        }
    )

    assert outcome.normalized["engine_code"] == "B4204T"
    assert outcome.normalized["engine_family_code"] == "VEA"
    assert outcome.normalized["engine_family_name"] == "Volvo Engine Architecture"
    assert outcome.normalized["power_kw"] == 145
    assert outcome.normalized["power_source_unit"] == "kw"
    assert outcome.normalized["displacement_cc"] == 1969
    assert outcome.normalized["displacement_source_unit"] == "ccm"


def test_metric_power_and_litre_displacement_convert_using_documented_units() -> None:
    outcome = normalize_ts_record(
        {"manufacturer": "BMW", "power_ps": "200", "displacement_l": "1,998"}
    )

    assert outcome.normalized["power_kw"] == 147
    assert outcome.normalized["power_source_unit"] == "metric_hp"
    assert outcome.normalized["displacement_cc"] == 1998
    assert outcome.normalized["displacement_source_unit"] == "litre"


def test_measurement_boundaries_and_ambiguous_sources_route_to_review() -> None:
    malformed = normalize_ts_record({"manufacturer": "Volvo", "kw": 0, "ccm": "not-a-number"})
    ambiguous = normalize_ts_record(
        {"manufacturer": "Volvo", "kw": 100, "power_ps": 136, "ccm": 1969}
    )

    assert "power_kw" not in malformed.normalized
    assert "displacement_cc" not in malformed.normalized
    assert {"kw_malformed", "ccm_malformed"} <= set(malformed.review_reasons)
    assert "power_kw" not in ambiguous.normalized
    assert "power_source_ambiguous" in ambiguous.review_reasons


def test_hybrid_and_dual_fuel_keep_each_underlying_fuel() -> None:
    plug_in_hybrid = normalize_ts_record(
        {"manufacturer": "Volvo", "fuel1": "01", "fuel2": "03", "ev_config": "Laddhybrid"}
    )
    bi_fuel = normalize_ts_record(
        {"manufacturer": "Volvo", "fuel1": "01", "fuel2": "09", "fuel_combo": "B"}
    )

    assert plug_in_hybrid.normalized["energy_sources"] == ["petrol", "electricity"]
    assert plug_in_hybrid.normalized["electrification_type"] == "plug_in_hybrid"
    assert bi_fuel.normalized["energy_sources"] == ["petrol", "cng"]
    assert bi_fuel.normalized["fuel_combination"] == "bi_fuel"


def test_all_three_ts_fuel_fields_are_retained() -> None:
    outcome = normalize_ts_record(
        {
            "manufacturer": "Volvo",
            "fuel1": "01",
            "fuel2": "09",
            "fuel3": "03",
            "fuel_combo": "T",
        }
    )

    assert outcome.normalized["energy_sources"] == ["petrol", "cng", "electricity"]
    assert outcome.normalized["fuel_combination"] == "tri_fuel"
