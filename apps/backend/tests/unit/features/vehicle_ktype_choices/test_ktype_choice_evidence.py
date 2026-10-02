"""What a choice stores, how an evaluation is named, and when a choice goes stale."""

import json

from choice_test_support import VEHICLE_ID, candidate, inputs, lookup, stored

from api.app.features.vehicle_ktype_choices import evidence

# ------------------------------------------------------------------------- fingerprint


def test_the_fingerprint_is_stable_across_order_and_confidence() -> None:
    first = lookup()
    reordered = lookup(candidates=[candidate("B", confidence=0.31), candidate("A", confidence=0.5)],
                       confidence=0.5, verdict="Other words.", plate="XYZ999")

    assert evidence.fingerprint(first) == evidence.fingerprint(reordered)
    assert len(first.evidence_fingerprint) == 64
    assert first.evidence_fingerprint == evidence.fingerprint(first)


def test_the_fingerprint_changes_with_what_the_matcher_saw_or_concluded() -> None:
    base = lookup().evidence_fingerprint
    changed = [
        lookup(inputs=inputs(power_kw=110)),
        lookup(inputs=inputs(build_month=201502)),
        lookup(inputs=None),
        lookup(catalog_batch="batch-2"),
        lookup(top_ktype="B"),
        lookup(terminal="resolved"),
        lookup(candidates=[candidate("A"), candidate("B", conflicts=("power_kw",))]),
        lookup(candidates=[candidate("A"), candidate("B"), candidate("C")]),
        lookup(candidates=[candidate("A")]),
    ]

    prints = [item.evidence_fingerprint for item in changed]
    assert base not in prints
    assert len(set(prints)) == len(prints)


# ---------------------------------------------------------------------------- snapshot


def test_the_snapshot_holds_what_was_shown_and_no_identity() -> None:
    shown = lookup()

    snapshot = evidence.snapshot(shown, "abc1234")

    assert snapshot["schema"] == "manual-ktype-choice-evidence-v1"
    assert snapshot["catalog_batch"] == "batch-1"
    assert snapshot["automatic"] == {
        "terminal": "review_required", "bucket": "several", "confidence": 0.9, "top_ktype": "A",
        "verdict": "Too close to call.", "reason_codes": ["match:x"],
    }
    assert snapshot["inputs"]["power_kw"] == 140
    assert [item["ktype"] for item in snapshot["candidates"]] == ["A", "B"]
    assert snapshot["candidates"][0] == shown.candidates[0].model_dump(mode="json")
    assert snapshot["candidate_limit"] == 5
    assert snapshot["separating_fields"] == ["power_kw"]
    assert snapshot["overlaid_fields"] == {"power_kw": "ais"}
    assert snapshot["source_record_id"] == 7
    assert snapshot["versions"] == {"code": "abc1234", "confidence_policy": "confidence-routing-v1"}
    text = json.dumps(snapshot)
    for private in (VEHICLE_ID, "ABC123", "YV1BW84S1F1234567", "secret trace", "5F7H"):
        assert private not in text
    assert not {"plate", "vin", "vehicle_id", "other_vehicle_ids", "decision_trace"} & set(snapshot)


def test_a_car_stopped_before_matching_has_no_inputs_in_its_snapshot() -> None:
    snapshot = evidence.snapshot(lookup(inputs=None, candidates=[], top_ktype=None), "v")

    assert snapshot["inputs"] is None and snapshot["candidates"] == []


# ------------------------------------------------------------------------------ assess


def test_a_fresh_choice_needs_no_review() -> None:
    shown = lookup()
    row = stored(shown, reason="seen on the car")

    state = evidence.assess(row, shown, True, 3)

    assert (state.status, state.ktype, state.choice_id) == ("chosen", "A", row.choice_id)
    assert (state.reviewer, state.reason, state.created_at) == ("Ada", "seen on the car",
                                                               row.created_at)
    assert (state.catalog_batch, state.automatic_terminal, state.automatic_ktype) == (
        "batch-1", "review_required", "A")
    assert state.chosen_candidate == shown.candidates[0].model_dump(mode="json")
    assert (state.needs_review, state.stale_reasons, state.changed_inputs) == (False, [], [])
    assert state.history_count == 3


def test_each_stale_reason_is_named() -> None:
    shown = lookup()
    chosen = stored(shown)

    def reasons(today, row=chosen, in_catalog=True):  # type: ignore[no-untyped-def]
        state = evidence.assess(row, today, in_catalog, 1)
        assert state.needs_review == bool(state.stale_reasons)
        return state.stale_reasons

    assert reasons(lookup(catalog_batch="batch-2")) == ["catalog_batch_changed"]
    assert reasons(lookup(inputs=inputs(power_kw=110))) == ["evidence_changed"]
    gone = lookup(candidates=[candidate("B"), candidate("C")])
    assert reasons(gone) == ["ktype_not_a_candidate"]
    assert reasons(gone, in_catalog=False) == ["ktype_not_in_catalog"]
    # Still offered: the catalog is not asked (a candidate-only KType is not in it).
    assert reasons(shown, in_catalog=False) == []
    # A conflict appearing, or the matcher now resolving, does not flag by itself.
    assert reasons(lookup(candidates=[candidate("A", conflicts=("power_kw",)), candidate("B")])) == []
    assert reasons(lookup(terminal="resolved", top_ktype="B")) == []
    # A new candidate beside the chosen one does not flag a chosen KType.
    assert reasons(lookup(candidates=[candidate("A"), candidate("C")])) == []

    assert reasons(
        lookup(catalog_batch="batch-2", inputs=inputs(power_kw=110), candidates=[candidate("B")])
    ) == ["catalog_batch_changed", "evidence_changed", "ktype_not_a_candidate"]


def test_changed_inputs_carry_then_and_now() -> None:
    row = stored(lookup())

    state = evidence.assess(row, lookup(inputs=inputs(power_kw=110, drive_type="awd")), True, 1)

    assert [(item.field, item.then, item.now) for item in state.changed_inputs] == [
        ("power_kw", 140, 110), ("drive_type", None, "awd")]


def test_matchable_becoming_not_matchable_is_an_evidence_change() -> None:
    matchable, stopped = lookup(), lookup(inputs=None, candidates=[], top_ktype=None)

    lost = evidence.assess(stored(matchable, "none"), stopped, True, 1)
    gained = evidence.assess(stored(stopped, "none"), lookup(candidates=[]), True, 1)

    assert lost.stale_reasons == ["evidence_changed"]
    assert [(item.field, item.then, item.now) for item in lost.changed_inputs] == [
        ("matchable", True, False)]
    assert [(item.field, item.then, item.now) for item in gained.changed_inputs] == [
        ("matchable", False, True)]
    assert evidence.assess(stored(stopped, "none"), stopped, True, 1).stale_reasons == []


def test_an_input_the_matcher_gained_later_does_not_flag_an_old_choice() -> None:
    shown = lookup()
    row = stored(shown)
    del row.evidence["inputs"]["build_month"]
    del row.evidence["inputs"]["electrification"]

    today = lookup(inputs=inputs(build_month=201502, electrification="hybrid"))

    assert evidence.assess(row, today, True, 1).stale_reasons == []


def test_none_of_these_is_flagged_only_by_a_new_candidate() -> None:
    shown = lookup()
    row = stored(shown, "none")

    assert evidence.assess(row, lookup(candidates=[candidate("A")]), True, 1).stale_reasons == []
    state = evidence.assess(row, lookup(candidates=[candidate("A"), candidate("C")]), False, 1)
    assert (state.status, state.ktype, state.chosen_candidate) == ("none", None, None)
    assert state.stale_reasons == ["new_candidates"]


def test_a_withdrawn_choice_has_no_reasons() -> None:
    row = stored(lookup(), "withdraw")
    moved_on = lookup(catalog_batch="batch-2", inputs=inputs(power_kw=1), candidates=[candidate("Z")])

    state = evidence.assess(row, moved_on, False, 2)

    assert (state.status, state.needs_review, state.stale_reasons) == ("withdrawn", False, [])
    assert state.changed_inputs == []


def test_a_code_version_change_alone_never_flags() -> None:
    shown = lookup()

    assert evidence.assess(stored(shown, code_version="older"), shown, True, 1).stale_reasons == []


# --------------------------------------------------------------------------- effective


def test_the_effective_ktype_is_the_persons_first_then_a_resolved_match() -> None:
    unresolved, resolved = lookup(), lookup(terminal="resolved", top_ktype="B")

    def state(action: str, today=unresolved):  # type: ignore[no-untyped-def]
        return evidence.assess(stored(unresolved, action), today, True, 1)

    assert evidence.effective(state("choose"), resolved) == ("A", "person")
    assert evidence.effective(state("none"), resolved) == (None, "person")
    assert evidence.effective(state("withdraw"), resolved) == ("B", "matcher")
    assert evidence.effective(state("withdraw"), unresolved) == (None, None)
    assert evidence.effective(None, resolved) == ("B", "matcher")
    assert evidence.effective(None, unresolved) == (None, None)
    assert evidence.effective(None, lookup(terminal="provisional")) == (None, None)
    stale = state("choose", lookup(catalog_batch="batch-2"))
    assert stale.needs_review and evidence.effective(stale, resolved) == ("A", "person")
