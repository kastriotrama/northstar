# Phonetic matching contract

SCRUM-92 adds a conservative phonetic recovery signal to the Stage 2 candidate
matcher. It helps genuine manufacturer and model spelling variation produce a
reviewable candidate; it never establishes identity by itself.

## Field boundary

The versioned `northstar-phonetic-v1` encoder accepts only these semantic fields:

- `manufacturer` and `manufacturer_alias`;
- `model` and `model_alias`.

VIN, registration/plate, TecDoc KType, engine code, type code and every other
structured identifier field return no phonetic signature. Inside model text,
only alphabetic tokens of at least three characters are encoded. This means
codes such as `XC90` and `B4204T` cannot become phonetic proof, while a human
name such as `Mazda 3` may use the `Mazda` token.

The encoder removes accents; maps `PH/F`, `SCH/SK`, `SH/S`, `CH/K`, `TH/T` and
soft `C/S`; groups similar consonants; removes non-leading vowels; and collapses
repeated sound groups. It has no external dependency and produces deterministic
sorted signatures.

## Matching behavior

If edit-based manufacturer scoping fails, a phonetic manufacturer overlap may
narrow the catalog to a `phonetic_manufacturer` scope. Ordinary model text must
still meet the configured candidate threshold. Like fuzzy/global manufacturer
scopes, phonetic manufacturer scope is always review-only.

For model names, phonetic overlap adds a small configured bonus only when the
ordinary text score already meets `phonetic_min_text_score`. It never creates a
candidate from a phonetic code alone. A candidate whose model required this
bonus receives `phonetic_match=true`, records `model_phonetic` in its evidence,
records the exact phonetic version, and remains review-only. Results also expose
the version when manufacturer scoping used phonetics.

Numeric model-series, year, fuel and engine conflicts are evaluated normally.
They remain authoritative over the phonetic signal. Candidate ordering, score
rounding, top-candidate margins and manufacturer boundaries remain the Stage 2a
contract defined in [fuzzy-matching-contract.md](fuzzy-matching-contract.md).
