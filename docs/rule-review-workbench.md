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

Source field and canonicalized source key remain immutable. Newly discovered
Brand values are not accepted automatically; a reviewer must classify the exact canonical key and
activate it before re-import.

## Current review backlog

The workbench reports reason counts for the latest passenger-car batch. Of 250
records, 101 require review: 75 need Brand evidence because Tillverkare is
missing, 23 have no manufacturer value, 2 contain an unknown manufacturer, and
1 contains an unresolved corporate group. This cohort has no bodywork/category
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
