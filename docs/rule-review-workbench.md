# Translation Rule Review Workbench

The **Rules** tab at `/normalization-review` lets reviewers inspect and correct
the reviewed Transportstyrelsen translation dictionary without editing source
files or overwriting imported evidence.

## Workflow

1. Select **Rules** and search or filter the rule dictionary.
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

## Safety boundaries

- An active version cannot be updated or deleted.
- Re-import is blocked while unapproved drafts exist.
- Re-import accepts 1–1,000 staged records and never modifies the source batch.
- Canonical values are restricted to the reviewed vocabulary for the selected
  canonical field.
- Raw identifiers remain outside the review API and screen.
- Creating new match terms or changing rule scope still requires a reviewed
  code change; this first workbench only corrects the output of existing rules.

The resulting batch can be searched and inspected from the **Vehicles** tab.
