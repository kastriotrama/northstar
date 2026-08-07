"""Conservative, field-aware canonicalization for staged source text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ingestion.normalization_pipeline import NormalizationContext, Transformer

TEXT_CANONICALIZATION_VERSION = "text-canonicalization-v1"

_NAME_FIELDS = frozenset(
    {
        "manufacturer",
        "base_manufacturer",
        "brand",
        "engine_family_name",
        "ktype_manufacturer",
        "model",
        "type",
        "variant",
        "version",
    }
)
_CODE_FIELDS = frozenset(
    {
        "body_code",
        "body_code2",
        "body_code_extra",
        "eu_category",
        "ev_config",
        "engine_code",
        "engine_family_code",
        "fuel1",
        "fuel2",
        "fuel3",
        "fuel_combo",
        "gearbox",
        "is_4wd",
        "vehicle_type",
    }
)
_TEXT_FIELDS = (
    _NAME_FIELDS
    | _CODE_FIELDS
    | frozenset(
        {
            "build_date",
            "build_month",
            "production_from",
            "production_to",
            "registration_date",
        }
    )
)
_WHITESPACE = re.compile(r"\s+")
_SAFE_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def canonicalize_text(field_name: str, value: object) -> str | None:
    """Return canonical text for an allow-listed field without guessing meaning."""

    if field_name not in _TEXT_FIELDS or not isinstance(value, str):
        return None
    canonical = unicodedata.normalize("NFKC", value)
    canonical = _WHITESPACE.sub(" ", canonical).strip()
    if field_name in _NAME_FIELDS:
        canonical = canonical.translate(_SAFE_PUNCTUATION_TRANSLATION)
    elif field_name in _CODE_FIELDS:
        canonical = canonical.upper()
    return canonical or None


def _applied_rule_ids(field_name: str, before: str, after: str) -> tuple[str, ...]:
    rules = ["TXT-NFKC-V1", "TXT-WHITESPACE-V1"]
    if field_name in _NAME_FIELDS:
        rules.append("TXT-PUNCT-SAFE-V1")
    if field_name in _CODE_FIELDS:
        rules.append("TXT-CASE-CODE-V1")
    return tuple(rules) if before != after else ()


@dataclass(frozen=True)
class TextCanonicalizationTransformer(Transformer):
    """Canonicalize safe fields on the working copy and retain raw evidence."""

    transformer_id: str = "ts.text-canonicalization"
    order: int = 5

    def apply(self, context: NormalizationContext) -> None:
        for field_name in sorted(_TEXT_FIELDS):
            before = context.canonical_record.get(field_name)
            if not isinstance(before, str):
                continue
            after = canonicalize_text(field_name, before)
            if after is None or before == after:
                continue
            context.canonical_record[field_name] = after
            context.record_change(
                transformer_id=self.transformer_id,
                target="canonical",
                field_name=field_name,
                rule_ids=_applied_rule_ids(field_name, before, after),
                before=before,
                after=after,
                confidence_effect=0.0,
            )
