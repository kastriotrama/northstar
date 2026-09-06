"""Evidence-gated exact model alias proposals for repeated unresolved cohorts."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[^A-Z0-9]+")


def _key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").upper())
    return _SEPARATORS.sub("", "".join(c for c in text if not unicodedata.combining(c)))


@dataclass(frozen=True)
class ExactModelAliasObservation:
    manufacturer: str
    source_model: str
    anchored_model: str | None
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class ExactModelAliasProposal:
    rule_id: str
    manufacturer: str
    source_term: str
    canonical_model: str
    anchor_count: int
    unresolved_count: int
    unresolved_reasons: tuple[str, ...]


def propose_exact_model_aliases(
    observations: tuple[ExactModelAliasObservation, ...],
    *,
    allowed_models_by_manufacturer: Mapping[str, Iterable[str]],
    minimum_anchor_count: int = 2,
    minimum_unresolved_count: int = 2,
) -> tuple[ExactModelAliasProposal, ...]:
    """Propose one exact alias only when repeated evidence has one catalog target."""

    if minimum_anchor_count < 1 or minimum_unresolved_count < 1:
        raise ValueError("proposal thresholds must be positive")
    allowed = {
        _key(manufacturer): {_key(model) for model in models if _key(model)}
        for manufacturer, models in allowed_models_by_manufacturer.items()
    }
    targets: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    target_display: dict[tuple[str, str, str], str] = {}
    unresolved: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    display: dict[tuple[str, str], tuple[str, str]] = {}
    for item in observations:
        manufacturer_key = _key(item.manufacturer)
        source_key = _key(item.source_model)
        if not manufacturer_key or len(source_key) < 3 or source_key.isdigit():
            continue
        key = (manufacturer_key, source_key)
        display.setdefault(key, (item.manufacturer.strip(), item.source_model.strip()))
        if item.anchored_model:
            canonical_key = _key(item.anchored_model)
            targets[key][canonical_key] += 1
            target_display.setdefault((*key, canonical_key), item.anchored_model.strip())
        if item.unresolved_reason:
            unresolved[key][item.unresolved_reason] += 1

    proposals: list[ExactModelAliasProposal] = []
    for key, reason_counts in unresolved.items():
        unresolved_count = sum(reason_counts.values())
        anchored = targets.get(key, Counter())
        if unresolved_count < minimum_unresolved_count or len(anchored) != 1:
            continue
        canonical_key, anchor_count = next(iter(anchored.items()))
        if anchor_count < minimum_anchor_count:
            continue
        if canonical_key == key[1] or canonical_key not in allowed.get(key[0], set()):
            continue
        manufacturer, source_term = display[key]
        digest = hashlib.sha256(f"{key[0]}:{key[1]}:{canonical_key}".encode()).hexdigest()[:14]
        proposals.append(
            ExactModelAliasProposal(
                rule_id=f"MOD-PROPOSED-{digest.upper()}",
                manufacturer=manufacturer,
                source_term=source_term,
                canonical_model=target_display[(*key, canonical_key)],
                anchor_count=anchor_count,
                unresolved_count=unresolved_count,
                unresolved_reasons=tuple(sorted(reason_counts)),
            )
        )
    return tuple(
        sorted(
            proposals,
            key=lambda item: (
                -item.unresolved_count,
                -item.anchor_count,
                item.rule_id,
            ),
        )
    )
