"""Reviewed pairs of the registry's power figure and TecDoc's for one electric drivetrain.

The registry records an electric car's rated power. TecDoc lists the peak the
same drivetrain reaches with boost or launch control (Audi A6 e-tron
performance: 270 kW rated, 280 kW peak), and for the first Polestar 2 the
registry gives one of its two motors where TecDoc gives both. Read as two
figures the pair is a power conflict, and the car gets no KType although its
model has exactly one KType per drivetrain.

Nothing here is derived. Every pair is listed, scoped to the maker and to the
catalog name of the model, and holds only between a car and a KType that are
both electric only (`fuzzy_matching` checks that). Within a model the
registry's figures and TecDoc's correspond one to one in ascending order and
by driven axles, which is what makes a pair the same drivetrain rather than a
neighbour. A model whose figures do not line up that way is not listed (BMW
iX1: one registry figure, two KTypes it could be).

Proposed 2026-10-07 from the cars stopped on power in the full register;
awaiting the data owner's confirmation (`REVIEWED_BY`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ingestion.tecdoc.engine_code_aliases import maker_key

REVIEWED_BY = "claude-proposal-2026-10-07 (awaiting data-owner review)"

_NON_ALPHANUMERIC = re.compile(r"[^A-Z0-9]+")


@dataclass(frozen=True)
class PowerEquivalence:
    """One model's reviewed pairs: registry kW -> TecDoc kW."""

    maker: str
    #: Matched against the catalog model name, upper case, punctuation as spaces.
    model: re.Pattern[str]
    pairs: dict[int, int]
    evidence: str


REVIEWED_POWER_EQUIVALENCES: tuple[PowerEquivalence, ...] = (
    PowerEquivalence(
        "AUDI", re.compile(r"^A6 E TRON\b"), {210: 240, 270: 280, 315: 340, 370: 405},
        "Rated against peak with launch control; rear-drive 210 and 270, quattro 315, S6 370. "
        "2,713 cars stopped on power.",
    ),
    PowerEquivalence(
        "AUDI", re.compile(r"^Q6 (SPORTBACK )?E TRON\b"), {315: 340, 360: 380},
        "The A6 e-tron's quattro drivetrain (315 / 340) and the SQ6 (360 / 380). 872 cars.",
    ),
    PowerEquivalence(
        "PORSCHE", re.compile(r"^MACAN XAB\b"), {250: 265, 285: 300, 330: 380},
        "Rated against overboost: Macan 250, Macan 4 285, Macan 4S 330. 1,038 cars.",
    ),
    PowerEquivalence(
        "PORSCHE", re.compile(r"^TAYCAN\b"), {280: 350, 360: 420},
        "Rated against overboost with the larger battery: Taycan 4 280, 4S 360 (2020-2023). "
        "The GTS and Turbo figures meet KTypes of two generations and are left out. 778 cars.",
    ),
    PowerEquivalence(
        "MINI", re.compile(r"^MINI COUNTRYMAN U25\b"), {225: 230},
        "Countryman SE ALL4: the model's only four-wheel-drive KType. 469 cars.",
    ),
    PowerEquivalence(
        "POLESTAR", re.compile(r"^POLESTAR 2\b"), {150: 300},
        "The 2020-2021 dual motor: the registry gives one of the two 150 kW motors. 1,017 cars.",
    ),
)


def _spaced(value: str) -> str:
    return _NON_ALPHANUMERIC.sub(" ", value.upper()).strip()


@lru_cache(maxsize=50_000)
def reviewed_power_equivalent(manufacturer: str, model: str, registry_kw: int) -> int | None:
    """TecDoc's figure for this registry figure on this maker's model, when reviewed."""

    maker, name = maker_key(manufacturer), _spaced(model)
    for equivalence in REVIEWED_POWER_EQUIVALENCES:
        if equivalence.maker == maker and equivalence.model.search(name):
            return equivalence.pairs.get(registry_kw)
    return None
