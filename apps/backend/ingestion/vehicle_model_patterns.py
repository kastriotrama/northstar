"""Reviewed patterns that read a model family out of the word registry text names it by.

The learned model rules (`vehicle_core_rules`) need siblings that already have a
model. Old and rare cars often have none: a 1988 Volvo registered as
"VOLVO 744-883 GL" or a BMW as "BMW 325" shares no key with a car TS named. These
patterns read the brand text's model word (`brand_token`) the way a person would.

Every pattern answers only with a model family TS itself uses for that make (the
vocabulary), so a filled vehicle looks exactly like one TS named. Before a pattern
rule is kept it is checked against the vehicles whose model is already known;
see `vehicle_core_rules.pattern_rules`.
"""

from __future__ import annotations

import re
from collections.abc import Collection

#: BMW names a car by series and engine: 320I, 525TDS, 118D -> 3, 5 and 1 Series.
_BMW_CODE = re.compile(r"([1-8])\d{2}[A-Z]{0,3}")
#: Mercedes-Benz names a car by class letter, alone or joined to its engine
#: ("C 180", "E220CDI"). ML is the M-Class.
_MERCEDES_CODE = re.compile(r"([ABCEGSV])(?:\d{3}[A-Z]{0,4})?")
_MERCEDES_WORDS = {"ML": "M-Class"}
#: Volvo type codes: the first two digits name the series and the third the body
#: (744 is a 740 saloon, 245 a 240 estate), optionally followed by an engine
#: code ("744-883") or run together with one ("1421341", a 142).
_VOLVO_CODE = re.compile(r"(24[2-5]|26[2-5]|70[4-5]|74[4-5]|76[0-5]|78[0-2]|94[4-5]|96[4-5]|14[2-5])(?:[-/]?\d+)?")
_VOLVO_SERIES = {"70": "760"}


def pattern_model(manufacturer: str, token: str, vocabulary: Collection[str]) -> str | None:
    """The model family `token` names for this make, or None when it names none.

    `vocabulary` is the make's model families as TS spells them; a pattern whose
    answer TS never uses is not an answer.
    """

    word = token.strip().upper()
    if not word:
        return None
    by_key = {name.upper(): name for name in vocabulary}
    # The word is a model TS already names this make's cars by ("COROLLA", "307").
    if word in by_key:
        return by_key[word]
    answer: str | None = None
    make = manufacturer.strip().upper()
    if make == "BMW" and (match := _BMW_CODE.fullmatch(word)):
        answer = f"{match.group(1)} Series"
    elif make == "MERCEDES-BENZ":
        if word in _MERCEDES_WORDS:
            answer = _MERCEDES_WORDS[word]
        elif match := _MERCEDES_CODE.fullmatch(word):
            answer = f"{match.group(1)}-Class"
    elif make == "VOLVO" and (match := _VOLVO_CODE.fullmatch(word)):
        code = match.group(1)
        answer = _VOLVO_SERIES.get(code[:2], f"{code[:2]}0")
    if answer is None:
        return None
    return by_key.get(answer.upper())
