"""The NorthStar canonical vocabulary, assembled from every source that feeds it.

Both Transportstyrelsen and TecDoc are normalized into our own token set and
written to the core graph; neither is the other's target. The code did not say
so evenly. ``api.app.features.match_review.field_resolution._canonical_values``
derives the allowed values for a field from the reviewed *TS* rule set alone,
so a token only TecDoc produces -- ``hybrid_petrol``, ``fwd`` -- is not in the
vocabulary that screen enforces, and ``TARGET_VOCABULARIES`` restates
``("fwd", "rwd", "awd")`` by hand to paper over exactly that.

This module makes the vocabulary a property of the graph rather than of one
source. A term is canonical when any registered source vouches for it, and each
term records who does. That turns a silent divergence into a listed one:
``coverage_report`` names the tokens only one side produces, which is the set a
reviewer has to rule on before matching can compare the two.

Nothing here invents a token. Every term is read from a source's own reviewed
mapping, so this listing cannot drift from what the pipelines emit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from ingestion.tecdoc import reference_data
from ingestion.translation_dictionaries import (
    REVIEWED_RULE_SET_VERSION,
    load_translation_rule_set,
)

#: Every source system normalized into the canonical vocabulary. The database
#: `core.vocabulary_alignments` check constraint carries the same two values.
SOURCE_SYSTEMS: tuple[str, ...] = ("transportstyrelsen", "tecdoc")


@dataclass(frozen=True)
class CanonicalTerm:
    """One canonical value, and every source whose reviewed mapping emits it."""

    canonical_field: str
    canonical_value: str
    vouched_by: tuple[str, ...]

    @property
    def is_shared(self) -> bool:
        """True when more than one source produces this token.

        A shared token can be compared across sources directly. A token only one
        source produces cannot -- not because it is wrong, but because nothing on
        the other side can ever equal it.
        """

        return len(self.vouched_by) > 1


@dataclass(frozen=True)
class FieldCoverage:
    """What each source contributes to one canonical field."""

    canonical_field: str
    shared: tuple[str, ...]
    transportstyrelsen_only: tuple[str, ...]
    tecdoc_only: tuple[str, ...]

    @property
    def is_aligned(self) -> bool:
        return not self.transportstyrelsen_only and not self.tecdoc_only


def _ts_terms() -> dict[str, set[str]]:
    """Canonical values the reviewed TS rule set produces, by canonical field."""

    rule_set = load_translation_rule_set(REVIEWED_RULE_SET_VERSION)
    terms: dict[str, set[str]] = {}
    for rule in rule_set.rules:
        if rule.canonical_value:
            terms.setdefault(rule.canonical_field, set()).add(rule.canonical_value)
    return terms


def _tecdoc_terms() -> dict[str, set[str]]:
    """Canonical values the reviewed TecDoc mappings produce, by canonical field.

    Read from `reference_data` rather than restated, so adding a key-table
    mapping there widens the vocabulary here without a second edit.
    ``tests/unit/ingestion/test_tecdoc_canonical_rules.py`` fails when a
    reviewed mapping is added that this function does not read.
    """

    return reference_data.reviewed_mapping_values()


@lru_cache(maxsize=1)
def canonical_terms() -> tuple[CanonicalTerm, ...]:
    """Every canonical term, with the sources that vouch for it."""

    by_source: Mapping[str, dict[str, set[str]]] = {
        "transportstyrelsen": _ts_terms(),
        "tecdoc": _tecdoc_terms(),
    }
    vouchers: dict[tuple[str, str], list[str]] = {}
    for source in SOURCE_SYSTEMS:
        for field, values in by_source[source].items():
            for value in values:
                vouchers.setdefault((field, value), []).append(source)
    return tuple(
        CanonicalTerm(field, value, tuple(sources))
        for (field, value), sources in sorted(vouchers.items())
    )


def canonical_values(canonical_field: str) -> tuple[str, ...]:
    """Every value a field may hold, whichever source produces it.

    Prefer this over reading one source's rule set: a target vocabulary derived
    from TS alone rejects tokens TecDoc legitimately emits.
    """

    return tuple(
        term.canonical_value
        for term in canonical_terms()
        if term.canonical_field == canonical_field
    )


def canonical_fields() -> tuple[str, ...]:
    """Every field with at least one canonical term."""

    return tuple(sorted({term.canonical_field for term in canonical_terms()}))


def coverage_report() -> tuple[FieldCoverage, ...]:
    """Per field, which tokens are shared and which only one source can produce.

    A token in `tecdoc_only` or `transportstyrelsen_only` is not a defect on its
    own -- TS records trailer bodies TecDoc has no concept of -- but it is the
    complete list of places where an equality test between the two sources can
    never succeed, which is what a reviewer needs before trusting a field to
    discriminate.
    """

    report: list[FieldCoverage] = []
    for field in canonical_fields():
        terms = [term for term in canonical_terms() if term.canonical_field == field]
        report.append(
            FieldCoverage(
                canonical_field=field,
                shared=tuple(t.canonical_value for t in terms if t.is_shared),
                transportstyrelsen_only=tuple(
                    t.canonical_value for t in terms if t.vouched_by == ("transportstyrelsen",)
                ),
                tecdoc_only=tuple(
                    t.canonical_value for t in terms if t.vouched_by == ("tecdoc",)
                ),
            )
        )
    return tuple(report)
