from scripts.diagnose_ts_tecdoc_recovery_cohorts import source_evidence_profile


def test_source_evidence_profile_exposes_only_field_presence() -> None:
    raw = {
        "plate": "ABC123",
        "vin": "SECRET",
        "brand": "Volvo",
        "model": "",
        "variant": "P246",
        "eeg_type_approval": "e4*2018/858*1234",
    }

    profile = source_evidence_profile(raw)

    assert profile == "brand+variant+eeg_type_approval"
    assert "ABC123" not in profile
    assert "SECRET" not in profile


def test_source_evidence_profile_reports_none() -> None:
    assert source_evidence_profile({"plate": "ABC123"}) == "none"
