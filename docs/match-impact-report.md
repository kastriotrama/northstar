# Match impact report

A repeatable measurement of how many cars the matcher resolves to one KType.
Run it before and after every matcher or data change and compare the two runs.
It is read-only.

## Run

From `apps/backend`:

```bash
python -m scripts.match_impact_report \
  --catalog-batch tecdoc-0326-canonical-full-prod-v2-20260914 \
  --label baseline --size 30000 --workers 3 \
  --out ../../outputs/match-impact/baseline.json
```

Then, after a change, the same command with a new `--label`, a new `--out` and
`--baseline ../../outputs/match-impact/baseline.json`.

- **Same cars every time.** The sample is ordered by a hash of `--seed` and the
  NOR ID, so the same seed over the same vehicles picks the same cars. Keep the
  default seed unless you want a second, independent sample.
- **Catalog pinned.** `--catalog-batch` is required. The newest batch in a
  database can be a test batch. Reports on different batches refuse to compare.
- **Population.** Registered vehicles in `--scope` (default `passenger`);
  `--include-deregistered` adds deregistered ones.
- **Speed.** About 20 cars a second with 3 workers: 30,000 cars in roughly
  25 minutes. The margin on a share is about ±0.5 points at 30,000 cars.

## What it reports

- **Terminals.** The matcher's own outcome per car. `resolved` is automatic
  resolution; against a baseline each line shows the change in points.
- **Engine agreement.** For each resolved car: do the KType's TecDoc engines
  include the car's engine code (compared without spaces, hyphens, dots, and
  with TecDoc's bracket parts)? A proxy for accuracy. It is independent only for
  changes that do not themselves use the engine code.
- **Against the baseline.** Car by car: gained, lost, and resolved to a
  different KType.
- **Why not resolved.** The most common blocking reason codes.
- **Resolved by manufacturer.** The 15 largest manufacturers in the sample.

The JSON file holds every sampled car's terminal and KType, so any two reports
of the same sample can be compared later.

## Reference set

`--reference cars.csv` with columns `vehicle_id,ktype_reference`: cars whose
correct KType a person has confirmed. Reference cars outside the sample are
evaluated too but counted only against the reference: correct, wrong, not
resolved. No reference set exists yet. It must be reviewed by a person, never
generated from the matcher's own output.
