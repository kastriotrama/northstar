# Fuzzy matching contract

SCRUM-91 adds deterministic candidate generation between normalized source
records and a supplied canonical/TecDoc candidate catalog. It does not accept a
vehicle identity, write graph relationships, or replace later confidence and
review decisions.

## Candidate scope

The immutable index groups candidates by normalized manufacturer name and
explicit manufacturer aliases. A recognized name or alias searches only that
manufacturer. A close manufacturer spelling may search a fuzzy manufacturer
scope, while a missing or unknown manufacturer falls back to the global index.
Fuzzy and global scopes are always review-only, even when their top model score
is high. This prevents a common model name from crossing manufacturer boundaries
and silently resolving to the wrong vehicle.

Each candidate provides an opaque reference, candidate type, manufacturer,
model and optional model aliases, production years, fuels and engine codes. The
index rejects duplicate references and sorts all internal collections so input
ordering cannot change output ordering.

## Scoring and evidence

Model similarity combines normalized Damerau-Levenshtein edit similarity with
token Jaccard similarity. The best canonical model or alias label becomes the text
score. Short, single-token model codes use edit similarity directly. Conflicting
numeric model series such as `XC40` and `XC90` receive an explicit penalty so a
one-character series change is not treated like a harmless typo. Available
context then adjusts that score:

- production year inside or outside the candidate range;
- overlapping or conflicting underlying fuels;
- exact or conflicting structured engine code.

Missing candidate context is reported but does not count as supporting or
conflicting evidence. Scores are clamped to `0..1`, rounded to six decimals,
then ordered by descending score and ascending candidate reference. This makes
ties deterministic.

All thresholds, weights, context bonuses/penalties, candidate limits and the
minimum top-candidate margin are constructor-injected through
`FuzzyMatchConfig`. The defaults are Phase 1 matching defaults, not final
pipeline confidence policy.

## Safe outcome

`eligible_for_auto_resolution` means only that the candidate passed this Stage
2a gate. Eligibility requires:

1. an exact manufacturer scope;
2. no year, fuel or engine conflict;
3. the configured automatic score;
4. the configured margin over the next candidate.

Later matching/review work must still decide whether to accept the identity.
Candidates below the minimum score stay unresolved. Fuzzy/global scope,
context conflicts, low automatic scores and narrow margins remain review-only.
Candidate payloads follow the existing review-queue shape and contain only
non-sensitive matching evidence.
