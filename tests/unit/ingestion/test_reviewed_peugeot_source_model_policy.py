import hashlib
import json
from pathlib import Path

from ingestion.tecdoc.source_model_rules import (
    ReviewedSourceModelPolicy,
    reviewed_source_model_policy,
)


MANIFEST = (
    Path(__file__).parents[3]
    / "ingestion/reviewed_source_model_policies/peugeot_3008_hns_reviewed_v1_20260831.json"
)


def _load_policy() -> ReviewedSourceModelPolicy:
    payload = json.loads(MANIFEST.read_text())
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return reviewed_source_model_policy(
        payload, expected_version=payload["version"], expected_digest=checksum
    )


def test_exact_reviewed_hnsu_signatures_recover_only_the_3008_ii_family() -> None:
    policy = _load_policy()
    common = {
        "type_text": "M", "variant": "R", "version": "HNSU-C16E00",
    }
    for extension in ("25", "26"):
        resolution = policy.resolve(
            manufacturer="PEUGEOT", source_model="3008",
            source_evidence={
                **common, "eeg_type_approval": f"e2*2007/46*0534*{extension}"
            },
        )
        assert resolution.target_model == "3008 II SUV (MC_, MR_, MJ_, M4_)"
        assert len(resolution.rule_ids) == 1
        assert resolution.conflict is False


def test_reviewed_hnsu_policy_does_not_generalize_approval_or_version() -> None:
    policy = _load_policy()
    evidence = {
        "eeg_type_approval": "e2*2007/46*0534*27",
        "type_text": "M", "variant": "R", "version": "HNSU-C16E00",
    }
    assert policy.resolve(
        manufacturer="PEUGEOT", source_model="3008", source_evidence=evidence
    ).target_model is None
    evidence["eeg_type_approval"] = "e2*2007/46*0534*25"
    evidence["version"] = "HPY-C16E00"
    assert policy.resolve(
        manufacturer="PEUGEOT", source_model="3008", source_evidence=evidence
    ).target_model is None
