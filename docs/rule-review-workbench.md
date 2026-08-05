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
6. Re-import the current normalized batch. NorthStar clones the original raw
   staging records into a new batch and normalizes the clone with the active
   version. The screen shows before/after status totals.

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

The workbench reports reason counts for the latest batch. The initial 162
review-required vehicles contained 106 exact legacy Brand values with no usable
Tillverkare, 55 other Brand values without enough corroborating evidence, and
85 bodywork/category conflicts. The reviewed legacy Brand catalog now maps all
106 to a canonical manufacturer. Re-import reduced review-required vehicles to
127 and eliminated `manufacturer_missing`; 71 of the corrected vehicles remain
in review only because they also have a bodywork/category conflict. Next, review
the remaining 55 evidence-gated Brand values and validate bodywork by category.
Because signals overlap, reason counts must not be added together.

## Safety boundaries

- An active version cannot be updated or deleted.
- Re-import is blocked while unapproved drafts exist.
- Re-import accepts 1–1,000 staged records and never modifies the source batch.
- Canonical values are restricted to the reviewed vocabulary for the selected
  canonical field.
- Raw identifiers remain outside the review API and screen.
- Translation-rule match terms and scope still require a reviewed code change.
  Manufacturer candidates already observed in the selected batch can be
  classified from the workbench because their exact source value is retained.

The resulting batch can be searched and inspected from the **Vehicles** tab.
