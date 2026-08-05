# Translation Rule Review Workbench

The **Rules** tab at `/normalization-review` lets reviewers inspect and correct
the reviewed Transportstyrelsen translation dictionary without editing source
files or overwriting imported evidence.

## Workflow

1. Select **Rules**, then choose **Translation rules** or **Manufacturer
   entities**.
2. Select a rule. Its source fields, source terms, vehicle scope, and
   manufacturer scope are read-only so a correction cannot silently broaden
   what the rule matches.
3. Choose a canonical value from that field's existing reviewed vocabulary,
   set the decision, optionally adjust its display label, and add a change note.
4. Save the change as a draft. Drafts do not affect normalization.
5. Add an activation note and activate all reviewed drafts together. Activation
   creates an immutable rule-set version and retains earlier approved overrides.
6. Re-import the current normalized batch. NorthStar clones only eligible
   passenger-car records (`M1`/`M1G`, or `PB` when category is absent) into a
   new batch and normalizes the clone with the active version. The screen shows
   before/after status totals.

## Manufacturer entities

The manufacturer view combines the reviewed Tillverkare catalog with exact
unresolved manufacturer or Brand values discovered in the current batch. A
reviewer can classify each exact entity as:

- vehicle manufacturer: use the reviewed canonical company;
- bodybuilder/converter: use `Tillverkare grundfordonet` as manufacturer and
  retain the converter separately;
- corporate group: require marketed-brand evidence;
- unknown: keep the record in review.

Selecting a Manufacturer entity shows its database lifecycle. `Created at` is
the first immutable rule version containing that entity; `Updated at` is the
latest version where its definition changed. Built-in catalog entries that
have never been persisted are labeled as unversioned rather than receiving an
invented timestamp.

Source field and canonicalized source key remain immutable. Newly discovered
Brand values are not accepted automatically; a reviewer must classify the exact canonical key and
activate it before re-import.

The active `MFR-MODEL-VARIANT-FALLBACK` policy handles the narrower case where
Tillverkare and Brand are both absent. It checks Model and Variant only against
reviewed manufacturer aliases, using a complete exact or prefix token match.
One unambiguous match records a provisional manufacturer plus confirmation
evidence; multiple manufacturers or unsafe substring-only matches remain in
review. A populated Brand is never overridden by this fallback.

The active `MFR-BRAND-PREFIX-FALLBACK` policy handles a missing Tillverkare
when Brand starts with an approved Manufacturer entity alias. Matching is
whole-token/prefix only and records a provisional manufacturer for later
corroboration. Reviewed aliases cover observed passenger-car variants such as
Saab, Škoda, Dacia, Jaguar, Mazda, Mitsubishi, Vauxhall, and VW. Compound
converter or marque terms (`ADRIA`, `DETHLEFFS`, and `DAIMLER`) are explicitly
kept in review rather than flattened into the prefix manufacturer.

## Current review backlog

The workbench reports reason counts for the latest passenger-car batch. Of 250
records, 37 are resolved, 207 are provisional, 6 require review, and none
failed. The remaining six are intentionally protected: Fiat/Adria,
Fiat/Dethleffs, Jaguar/Daimler, Knaus/FCA, Škoda's legal entity, and a
PSA/Citroën corporate-group case. This cohort has no bodywork/category
conflicts. Reason counts can overlap in future batches and must not be assumed
to equal the vehicle count without checking the records.

## Safety boundaries

- An active version cannot be updated or deleted.
- Re-import is blocked while unapproved drafts exist.
- Re-import accepts 1–1,000 eligible passenger-car records and never modifies
  the source batch. Other vehicle categories remain untouched and are not
  copied into the new review batch.
- Canonical values are restricted to the reviewed vocabulary for the selected
  canonical field.
- Raw identifiers remain outside the review API and screen.
- Translation-rule match terms and scope still require a reviewed code change.
  Manufacturer candidates already observed in the selected batch can be
  classified from the workbench because their exact source value is retained.

The resulting batch can be searched and inspected from the **Vehicles** tab.
