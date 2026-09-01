from api.app.features.match_review.patterns import build_inventory_patterns, build_review_patterns
from ingestion.match_pattern_inventory import observe_match_pattern
from ingestion.tecdoc.match_run_adapters import MatchEvaluation


def test_bodywork_patterns_are_plate_free_and_group_repeated_mappings() -> None:
    items = [
        {
            "category": "bodywork_conflict",
            "source_evidence": {"plate": plate, "brand": brand, "model": model, "body_code": "AC"},
            "candidate_matches": [{"candidate_reference": reference, "evidence": {}}],
            "reason_codes": ["context_conflict:bodywork"],
        }
        for plate, brand, model, reference in (
            ("AAA001", "VOLVO", "XC60", "1"),
            ("AAA002", "KIA", "NIRO", "2"),
        )
    ]
    contexts = {
        "1": {"candidate_reference": "1", "bodyworks": ["SUV"]},
        "2": {"candidate_reference": "2", "bodyworks": ["SUV"]},
    }

    patterns = build_review_patterns(items, contexts, {"bodywork_conflict": 1200})

    assert len(patterns) == 1
    assert patterns[0]["title"] == "TS body code AC → TecDoc SUV"
    assert patterns[0]["sample_occurrences"] == 2
    assert patterns[0]["category_occurrences"] == 1200
    assert "plate" not in str(patterns[0]).lower()


def test_model_patterns_remain_manufacturer_scoped() -> None:
    items = [
        {
            "category": "model_unmatched",
            "source_evidence": {"brand": "BMW", "model": "IX M60"},
            "candidate_matches": [],
            "reason_codes": ["match:no_candidates"],
        }
    ]

    pattern = build_review_patterns(items, {}, {"model_unmatched": 9})[0]

    assert pattern["source_values"] == {"manufacturer": "BMW", "model": "IX M60"}
    assert pattern["title"] == "BMW IX M60 → none"


def test_inventory_observation_is_plate_free_and_deterministic() -> None:
    observation = observe_match_pattern(
        {"plate": "AAA001", "brand": "BMW", "model": "IX M60", "body_code": "AC"},
        MatchEvaluation(
            "review_required",
            ("match:no_candidate_above_threshold",),
        ),
    )

    assert observation is not None
    assert observation.category.code == "model_unmatched"
    assert "plate" not in str(observation.evidence).lower()
    assert observation.example["manufacturer"] == "BMW"


def test_inventory_patterns_report_exhaustive_coverage() -> None:
    patterns = build_inventory_patterns(
        [{
            "pattern_key": "model_unmatched:abc",
            "blocker_category": "model_unmatched",
            "pattern_evidence": {
                "source_values": {"manufacturer": "BMW", "model": "IX M60"},
                "candidate_values": {"candidate_count": 0, "candidate_references": []},
            },
            "occurrence_count": 17,
            "examples": [{"manufacturer": "BMW", "model": "IX M60", "candidate_reference": None}],
        }],
        {"model_unmatched": 80},
    )

    assert patterns[0]["coverage"] == "exhaustive"
    assert patterns[0]["sample_occurrences"] == 17
    assert patterns[0]["category_occurrences"] == 80
