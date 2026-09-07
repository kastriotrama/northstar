from ingestion.rule_contract import validate_rule_contract


def _contract() -> dict[str, object]:
    return {
        "processing_order": ["manufacturer", "review"],
        "dictionaries": {"manufacturers": {"BUIK": "Buick"}},
        "rules": [{
            "rule_id": "MFR-BUIK-FAB-BU-V1", "stage": "manufacturer", "priority": 10,
            "match": {"all": [
                {"field": "raw.brand", "operator": "regex", "value": "^BUIK\\b"},
                {"field": "raw.fab_code", "operator": "equals", "value": "BU"},
            ]},
            "actions": [{"op": "set", "field": "normalized.manufacturer", "value": "Buick"}],
        }],
    }


def test_valid_rule_contract_has_no_semantic_errors() -> None:
    assert validate_rule_contract(_contract()) == ()


def test_rule_contract_rejects_ambiguous_invalid_and_terminal_actions() -> None:
    contract = _contract()
    rule = contract["rules"][0]  # type: ignore[index]
    duplicate = dict(rule)
    duplicate["actions"] = [
        {"op": "exclude"},
        {"op": "set", "field": "normalized.manufacturer", "value": "Buick"},
    ]
    contract["rules"] = [rule, duplicate]
    condition = rule["match"]["all"][0]
    condition["field"] = "raw.base_manufactuer"
    condition["operator"] = "regexp"

    errors = validate_rule_contract(contract)

    assert any("duplicate rule_id" in error for error in errors)
    assert any("ambiguous duplicate stage/priority" in error for error in errors)
    assert any("invalid readable field" in error for error in errors)
    assert any("invalid operator" in error for error in errors)
    assert any("action follows a terminal action" in error for error in errors)
