from api.app.features.match_review.patterns import build_review_patterns


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
