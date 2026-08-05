"""Conservative phonetic signatures for human vehicle names only."""

from __future__ import annotations

import re
import unicodedata

PHONETIC_VERSION = "northstar-phonetic-v1"

_ALLOWED_FIELDS = frozenset({"manufacturer", "manufacturer_alias", "model", "model_alias"})
_SOURCE_TOKEN = re.compile(r"[A-Z0-9]+")
_NON_ASCII_LETTER = re.compile(r"[^A-Z]+")
_DUPLICATE_CHARACTERS = re.compile(r"(.)\1+")
_VOWELS = frozenset("AEIOUY")
_GROUPS = {
    **dict.fromkeys("BPFVW", "P"),
    **dict.fromkeys("CGJKQX", "K"),
    **dict.fromkeys("DT", "T"),
    **dict.fromkeys("SZ", "S"),
    "L": "L",
    "M": "M",
    "N": "N",
    "R": "R",
    "H": "",
}


def _ascii_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.upper())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _encode_token(token: str) -> str:
    token = token.replace("PH", "F").replace("SCH", "SK").replace("SH", "S")
    token = token.replace("CH", "K").replace("TH", "T")
    token = re.sub(r"C(?=[EIY])", "S", token)
    token = _NON_ASCII_LETTER.sub("", token)
    if len(token) < 3:
        return ""
    encoded: list[str] = []
    for index, character in enumerate(token):
        if character in _VOWELS:
            mapped = character if index == 0 else ""
        else:
            mapped = _GROUPS.get(character, character)
        if mapped and (not encoded or encoded[-1] != mapped):
            encoded.append(mapped)
    return _DUPLICATE_CHARACTERS.sub(r"\1", "".join(encoded))


def phonetic_signature(value: object, *, field_name: str) -> tuple[str, ...] | None:
    """Return stable phonetic codes only for allow-listed human-name fields."""

    if field_name not in _ALLOWED_FIELDS or not isinstance(value, str):
        return None
    tokens = tuple(
        token
        for token in _SOURCE_TOKEN.findall(_ascii_text(value))
        if len(token) >= 3 and token.isalpha()
    )
    codes = tuple(sorted({code for token in tokens if (code := _encode_token(token))}))
    return codes or None


def has_phonetic_overlap(
    left: object,
    right: object,
    *,
    left_field: str,
    right_field: str,
) -> bool:
    left_signature = phonetic_signature(left, field_name=left_field)
    right_signature = phonetic_signature(right, field_name=right_field)
    if left_signature is None or right_signature is None:
        return False
    return bool(set(left_signature) & set(right_signature))
