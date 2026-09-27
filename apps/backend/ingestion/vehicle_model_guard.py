"""Keep a learned model from contradicting the car it would fill.

The model rules (`vehicle_core_rules`) learn from vehicles TS gave a model. TS
normalization never named some models -- Bora, Jetta, Sharan, Sintra, Carens -- so
a key those cars share with a sibling (a VIN prefix, a type code) was learned from
the sibling alone and fills the sibling's model: every "VW BORA 1,6" became a Golf.
The holdout check cannot see this, since it only holds vehicles with a model.

The car's own registry text can. A fill is refused when the text's model word --
the registry model field, or the word after the make -- names a catalog model of
another family, read as the matcher reads it: "VW BORA 1,6" names BORA I, not a
Golf. A trim word elsewhere ("200 T", "1 6 FSI") is weaker evidence than the rule
and never refuses it.

The build year is no check: the catalog lacks many eras of models TS names rightly
(a 1969 Pontiac GTO, a 1988 Mercedes-Benz 250 D that TecDoc calls E-Class only
from 1993), so a year gap says more about the catalog than about the fill.

Families are compared by name, tolerant of how TS and TecDoc spell them: "3 Series"
is the family of "3 (E46)", "Ceed" of "CEE'D (ED)", "Mazda3" of "3 (BK)".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import Connection

from ingestion.active_rules import load_active_rules
from ingestion.fuzzy_matching import same_model_family
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator, load_postgres_ktype_catalog
from ingestion.tecdoc.model_aliases import ReviewedModelAliasIndex


@dataclass(frozen=True)
class Verdict:
    """Why a model does not fit a car, or None when nothing contradicts it."""

    reason: str
    detail: str


class ModelGuard:
    """Judges a learned model against the model word in the car's own text."""

    def __init__(self, evaluator: TecDocDryRunEvaluator) -> None:
        self._evaluator = evaluator
        self._cache: dict[tuple[Any, ...], Verdict | None] = {}

    def verdict(
        self,
        *,
        manufacturer: str | None,
        model_family: str,
        evidence: Mapping[str, str | None],
    ) -> Verdict | None:
        texts = tuple(sorted((field, str(value)) for field, value in evidence.items() if value))
        key = (manufacturer, model_family, texts)
        if key not in self._cache:
            self._cache[key] = self._judge(manufacturer, model_family, dict(texts))
        return self._cache[key]

    def _judge(
        self, manufacturer: str | None, model_family: str, evidence: Mapping[str, str]
    ) -> Verdict | None:
        if not manufacturer:
            return None
        read = self._evaluator.source_text_model(manufacturer, evidence)
        if read is not None and not same_model_family(model_family, read[1], read[0]):
            return Verdict("text_names_another_model", read[1])
        return None


def build_model_guard(connection: Connection, batch_id: str) -> ModelGuard:
    """The guard over one pinned catalog batch, with the matcher's own reading rules."""

    catalog = load_postgres_ktype_catalog(connection, batch_id=batch_id)
    rule_set, manufacturer_rules = load_active_rules(connection)
    return ModelGuard(TecDocDryRunEvaluator(catalog, manufacturer_rules, ReviewedModelAliasIndex(rule_set)))


def count_verdicts(verdicts: Iterable[Verdict | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in verdicts:
        if verdict is not None:
            counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
    return counts
