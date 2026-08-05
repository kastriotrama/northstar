# Golden normalization and reconciliation corpus

SCRUM-94 freezes representative Phase 1 behavior in a versioned, sanitized corpus. It is
an approval boundary for normalization and TS-to-TecDoc candidate routing; it is not a source
of production data and does not contain plates, VINs, registration numbers, contact details or
other unnecessary personal data.

## Coverage

`tests/golden/normalization-reconciliation-v1.json` contains exactly 200 reviewed examples:

- 160 Transportstyrelsen normalization cases covering common mappings, dates, engine fields,
  measurements, transmission, bodywork, drive candidates, hybrid/dual-fuel evidence, malformed
  source values and unknown-manufacturer review routing.
- 40 reconciliation cases covering exact matches, supported inexact matches, hard fuel
  conflicts and equal-score ambiguity with retained candidate alternatives.

Each example has a stable ID, description and coverage tags. Normalization expectations include
the normalized and candidate fields, route/status, confidence, rule IDs, review reasons, complete
decision trace and rule-match evidence. Reconciliation expectations include Stage 2 scope and
reason plus the final route, confidence, selected reference, alternatives, hard conflicts and
complete confidence trace.

## Verification and regression review

Run the same command used by CI:

```bash
python -m ingestion.golden_corpus verify \
  tests/golden/normalization-reconciliation-v1.json
```

The command exits unsuccessfully on the first unapproved difference and prints a unified diff
whose filenames contain the stable case ID. This makes the affected evidence or output visible
in the pull-request check instead of reducing the failure to a count.

If a rule change is intentional, inspect the diff first and then explicitly regenerate approved
expectations:

```bash
python -m ingestion.golden_corpus approve \
  tests/golden/normalization-reconciliation-v1.json
```

The updated JSON file must be reviewed with the rule change. Approval is never performed by CI.
The generator in `scripts/generate_golden_corpus.py` defines the initial curated inputs; it is not
part of normal verification and should only be rerun when the reviewed corpus composition itself
changes.

## Safety gates

The runner rejects:

- fewer than 200 cases;
- duplicate or empty case IDs;
- untagged or undescribed examples;
- an unknown corpus or case kind;
- sensitive source field names anywhere in the document;
- malformed candidate/query inputs; and
- any expected-versus-actual difference not explicitly approved in the committed golden file.
