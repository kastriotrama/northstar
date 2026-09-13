"""The value-level TecDoc gap: which raw codes/labels have no canonical target.

`tecdoc_review.predicate.GAP_FIELDS` counts *KTypes* still missing a canonical
field -- the row-level question `ts-records`' unresolved summary answers. This
answers the question underneath it: which distinct raw value is the reason,
and how many KTypes carry it. That is the click target a reviewer resolves,
the same way TS's gap-groups screen turns a row count into a short list of
shapes worth ruling on.

Four fields are here: `energy_sources`, `bodywork_form`, `drive_type` --
matching exactly the three keys `canonical_rule_proposals.generate_rules`
builds a `reviewed_by_field` for, each with a *TecDoc*-side reviewed mapping
in `ingestion.tecdoc.reference_data` -- plus `transmission_type`, which has
no TecDoc-side mapping but does have a real target vocabulary: TS's side of
`canonical_vocabulary` already normalizes to `manual`/`automatic`/`cvt`/
`dct`/`amt`, so a TecDoc value can be ruled onto an existing term even before
any TecDoc-side reviewed dict exists. `manufacturer` / `model_family` are
open vocabulary; neither has a target to resolve toward at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from ingestion.tecdoc import reference_data
from ingestion.tecdoc.canonical_rule_proposals import comparison_key

RESOLVABLE_FIELDS: tuple[str, ...] = (
    "energy_sources",
    "bodywork_form",
    "drive_type",
    "transmission_type",
)


class TecDocResolveError(ValueError):
    """A resolve request named an unresolvable field, value or target."""


@dataclass(frozen=True)
class GapValueSpec:
    canonical_field: str
    key_table: str
    #: The raw value a reviewer rules on -- a KT082/KT086 code, or for fuel (no
    #: canonical dict is code-keyed) the KT088/182 official English label.
    value_expr: str
    #: A friendlier column shown alongside `value_expr`, or None when they
    #: already are the same thing.
    label_expr: str | None
    #: Only rows where this holds count toward the gap: the canonical field is
    #: genuinely unresolved, and the raw value being grouped on actually exists
    #: (a KType with no bodywork code at all is a different problem -- no data,
    #: not an unmapped value -- and does not belong in this list).
    unresolved_sql: str


GAP_VALUE_SPECS: dict[str, GapValueSpec] = {
    # A bodywork candidate node is written only when its code already resolves
    # (`canonical_promotion._promotion`: `bodywork_source_key = ... if
    # canonical_bodywork else None`) -- an unmapped code never gets a node, so
    # the raw code has to be read off the variant, which carries it
    # unconditionally, rather than off the bodywork entity, which may not exist.
    "bodywork_form": GapValueSpec(
        canonical_field="bodywork_form",
        key_table="086",
        value_expr="variant_attributes->>'tecdoc_body_type_code'",
        label_expr="variant_attributes->>'tecdoc_bodywork_official_label'",
        unresolved_sql=(
            "bodywork_attributes->>'canonical_name' IS NULL "
            "AND variant_attributes->>'tecdoc_body_type_code' IS NOT NULL"
        ),
    ),
    "drive_type": GapValueSpec(
        canonical_field="drive_type",
        key_table="082",
        value_expr="variant_attributes->>'tecdoc_drive_type_code'",
        label_expr="variant_attributes->>'tecdoc_drive_official_label'",
        unresolved_sql=(
            "variant_attributes->>'drive_type' IS NULL "
            "AND variant_attributes->>'tecdoc_drive_type_code' IS NOT NULL"
        ),
    ),
    # The official KT088 label (`tecdoc_engine_fuel_label`) is only written when
    # an engine actually links (`engine_link_status = 'linked'`); a KType whose
    # engine allocation is ambiguous or missing carries only the numeric KT182
    # vehicle-level code (`tecdoc_fuel_code`). Both are shown -- a code with no
    # label is still a real, resolvable gap, just one the reviewer has to read
    # off the KT182 table by number rather than by name.
    "energy_sources": GapValueSpec(
        canonical_field="energy_sources",
        key_table="088",
        value_expr=(
            "coalesce(variant_attributes->>'tecdoc_engine_fuel_label', "
            "'KT182 code ' || (variant_attributes->>'tecdoc_fuel_code'))"
        ),
        label_expr=None,
        unresolved_sql=(
            "coalesce(engine_attributes->>'fuel_type', variant_attributes->>'fuel_type', "
            "variant_attributes->>'vehicle_fuel_type') "
            "IS NULL AND coalesce(variant_attributes->>'tecdoc_engine_fuel_label', "
            "variant_attributes->>'tecdoc_fuel_code') IS NOT NULL"
        ),
    ),
    # No promotion ever computes a canonical transmission_type -- there is no
    # TecDoc-side reviewed dict to gate on, unlike bodywork/drive -- so the
    # transmission entity's own label always survives unconditionally, and
    # every KType carrying one counts as open until a reviewer rules on it.
    #
    # `transmission_attributes` (the joined `transmission` entity) only exists
    # for a `linked` allocation -- `linked_multiple`/`type_known` ktypes, and
    # every candidate-only one regardless of status, never get one written,
    # even though `_transmission_summary` already put the same label on
    # `variant_attributes` for all of them. Reading `transmission_attributes`
    # alone silently hid that majority from this browser and from
    # `generate-tecdoc-rules`' scan -- the same blind spot fixed for
    # `energy_sources`/`drive_type`.
    "transmission_type": GapValueSpec(
        canonical_field="transmission_type",
        key_table="085",
        value_expr=(
            "coalesce(transmission_attributes->>'transmission_type_name', "
            "variant_attributes->>'transmission_type_name')"
        ),
        label_expr=None,
        unresolved_sql=(
            "coalesce(transmission_attributes->>'transmission_type_name', "
            "variant_attributes->>'transmission_type_name') IS NOT NULL"
        ),
    ),
}


def blocked_reason(canonical_field: str, source_term: str) -> str | None:
    """Why this value must not be resolved with a single target, if any.

    A mixed TecDoc descriptor -- "Petrol/Electric" -- names a capability, not
    the fuel a vehicle uses; `EngineFuelEvidence` and `generate_rules` both
    already refuse to read it as a scalar resolution. A one-click Resolve here
    would quietly overrule that, so it is refused before it can be written.
    """

    if canonical_field != "energy_sources":
        return None
    mixed = {comparison_key(k) for k in reference_data.reviewed_mixed_engine_fuel_labels()}
    return "mixed_descriptor" if comparison_key(source_term) in mixed else None
