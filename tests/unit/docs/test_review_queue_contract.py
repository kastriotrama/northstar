from pathlib import Path

DOCUMENT = Path("docs/review-queue-design.md")


def test_review_queue_document_covers_story_contract() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    for required in (
        "SCRUM-18",
        "`core.review_queue`",
        "`pending`",
        "`in_review`",
        "`resolved`",
        "`rejected`",
        "`candidate_matches`",
        "`source_table`",
        "`source_record_id`",
        "review_queue_status_created_at_idx",
        "northstar-ingest migrate-review-queue",
    ):
        assert required in text, required


def test_routing_contract_defines_all_three_paths_and_hard_stops() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "Canonical graph" in text
    assert "Provisional" in text
    assert "Review queue" in text
    assert "Confidence ≥ `0.90`" in text
    assert "Confidence ≥ `0.70` and < `0.90`" in text
    assert "hard stops" in text
    assert "manufacturer and base-vehicle manufacturer roles" in text
    assert "No fake `Unknown` canonical nodes" in text


def test_examples_cover_current_manufacturer_and_hybrid_findings() -> None:
    text = DOCUMENT.read_text(encoding="utf-8")

    assert "Manufacturer versus bodybuilder" in text
    assert "manufacturer_role_unknown" in text
    assert "Petrol code plus explicit hybrid evidence" in text
    assert "`ELHYBRID`" in text
    assert "powertrain_signal_conflict" in text
