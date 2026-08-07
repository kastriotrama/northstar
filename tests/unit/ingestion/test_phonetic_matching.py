import pytest

from ingestion.phonetic_matching import (
    PHONETIC_VERSION,
    has_phonetic_overlap,
    phonetic_signature,
)


def test_representative_name_misspellings_have_stable_phonetic_overlap() -> None:
    assert PHONETIC_VERSION == "northstar-phonetic-v1"
    assert has_phonetic_overlap(
        "Mersedes",
        "Mercedes-Benz",
        left_field="manufacturer",
        right_field="manufacturer_alias",
    )
    assert has_phonetic_overlap(
        "Kamri",
        "Camry",
        left_field="model",
        right_field="model_alias",
    )
    assert has_phonetic_overlap(
        "Citroen",
        "Citroën",
        left_field="manufacturer",
        right_field="manufacturer_alias",
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "vin",
        "plate",
        "registration_number",
        "k_type",
        "tecdoc_k_type",
        "engine_code",
        "type_code",
    ],
)
def test_structured_identifier_fields_never_receive_phonetic_codes(field_name: str) -> None:
    assert phonetic_signature("WVWZZZ1JZXW000001", field_name=field_name) is None


def test_alphanumeric_model_codes_do_not_become_phonetic_proof() -> None:
    assert phonetic_signature("XC90", field_name="model") is None
    assert phonetic_signature("B4204T", field_name="model") is None
    assert phonetic_signature("WVWZZZ1JZXW000001", field_name="model") is None


def test_unrelated_names_do_not_overlap() -> None:
    assert not has_phonetic_overlap(
        "Volvo",
        "Toyota",
        left_field="manufacturer",
        right_field="manufacturer_alias",
    )
