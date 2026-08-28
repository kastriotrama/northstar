# TS→TecDoc next-gate evidence — 2026-08-28

This is a local, read-only evidence package. It does not activate a model or bodywork rule, promote a KType, attach an alias, or write a match decision.

## Independent Golf evidence

`outputs/scrum101-independent-approval-evidence-20260828.json` joins the public RDW TGK Handelsbenaming Fabrikant and TGK Carrosserie Uitvoering datasets by the exact approval, variant, version, and revision keys supplied by TS. It covers all 143 Golf rows in the proposal packet:

- 66 AU, 59 AUV, 16 CD and 2 CDV rows have an exact independent RDW join.
- All 143 are independently labelled `GOLF`/the same type code and body code `AC` in this join.
- No RDW body disagreement was observed, but the public name evidence does not distinguish a TecDoc KType. It therefore supports a source-body observation, not a blanket `GOLF → GOLF VARIANT` model rule.
- The TÜV/Pfalz approval document separately lists AUV for Golf R Variant and Golf Sportsvan under approval `e1*2007/46*0627*..`, demonstrating why AUV alone cannot select Variant. See the rendered page retained under `/tmp/northstar-evidence-6QPehM/golf-page7.png` and source PDF checksum in the local evidence directory.

The matching code treats partial approval prefixes, variant/version prefixes and wildcard-like suffixes as non-matches.

## Volvo proposal review

The existing 47 Volvo proposals remain `proposed` and unreviewed. The RDW/official evidence check preserves the following distinction:

- Manufacturer material describes XC40 and XC60 as SUVs, which is useful model-level context.
- It is not exact proof that every TS `AC` record and every proposed TecDoc KType shares the same bodywork interpretation.
- The packet contains five older XC60 approval-series rows and two national-approval rows; these remain separate review cohorts. No rule is activated.

Transportstyrelsen defines `AC` as stationsvagn (estate/station wagon), not SUV. The source and catalog values remain stored separately; any Volvo translation needs a reviewed, approval-scoped rule and must retain year/power/drive conflict gates.

## Mixed fuel and complete-source audit

`outputs/scrum101-mixed-fuel-source-audit-20260828.json` replayed all **72,570** TecDoc source KTypes and checked the 11 candidate-only KTypes. All 11 targets were found. Nine Saab targets use a unique source engine displacement, but their engine fuel descriptor is code `026`, official label `Petrol/Alcohol`; 2,332 active KType-engine relationships use this mixed descriptor. The representation now preserves the exact code, label, components `(petrol, alcohol_unspecified)`, source references and a null scalar fuel. It cannot be treated as petrol-only or ethanol, and it remains non-promotable. The diesel target has two distinct engine allocations and remains ambiguous.

## Gate outcome

The independent evidence gate is now reproducible and auditable. The safe next action is domain review of the exact evidence rows, followed by a separately versioned rule manifest. Rebuilding the candidate catalog is still blocked until that review and the mixed-fuel promotion contract are approved.
