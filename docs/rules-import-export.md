# Importing the rules bundle into your own database

This repo can export every reviewed rule — TS catalog decisions, manufacturer-entity
rulings, free-standing policies, and TecDoc's live resolution rules — into one JSON
file, and load that file into a different database. Use this when someone hands you
a `rules_export.json` (or you're handed the file directly) and you want those same
rules active in your local/dev database.

## What's in the bundle

- **`rule_policy`** — the active `core.translation_rule_versions` overrides: every
  accepted/proposed TS catalog decision, manufacturer-entity ruling, and free-standing
  policy (VIN+brand corrections, special-vehicle safety policies, etc.) layered on top
  of the hardcoded base rule set.
- **`tecdoc_resolution_rules`** — rules a reviewer authored live against one TecDoc
  value (`core.tecdoc_resolution_rules`).
- **`ts_resolution_rules`** — IF/THEN rules authored from the `/ts-data` resolve panel
  (`core.match_resolution_rules`).

Not included: the auto-generated TecDoc catalog (`core.tecdoc_rules`) — that's pipeline
output regenerated from data, not something to hand-copy between databases.

## Prerequisites

- The repo's Python virtualenv set up (`.venv`), run from `apps/backend`.
- Your `DATABASE_URL` (or ingestion settings' `.env`) pointing at *your own* database —
  double-check the port before running anything. `northstar-ingest` bare defaults to
  `5432`; this repo's local Postgres is usually `5433`.
- For the TS resolution rules section only: at least one completed build already in
  your database (`core.match_resolution_rules`' rules attach to a build). If you have
  none, that section is skipped automatically — the other two still import.

## Import the bundle

```bash
cd apps/backend
python -m scripts.import_all_rules rules_export.json
```

This is a **dry run** by default — it only reports what would change, it writes
nothing. Read the output, then actually apply it:

```bash
python -m scripts.import_all_rules rules_export.json --commit
```

Safe to run more than once:
- Policy overrides land as one new immutable version, and only if the merge actually
  differs from what your database already has.
- TecDoc resolution rules upsert on their natural key, and skip a row if your local
  copy was edited more recently than the one arriving (reported as a conflict, not
  silently overwritten).
- TS resolution rules skip any rule already present for the same source/target/conditions.

## Producing a new bundle (for whoever needs to export next)

```bash
cd apps/backend
python -m scripts.export_all_rules --out rules_export.json
```

Commit the resulting file if it should travel with the repo, or send it directly —
either way `import_all_rules.py` above is how the other side consumes it.

### Individual sections

Each section also has its own standalone export/import pair, if you only need one:

| Section | Export | Import |
| --- | --- | --- |
| Policy overrides | `scripts.export_rule_policy` | `scripts.load_rule_policy` |
| TecDoc resolution rules | `scripts.export_resolution_rules` | `scripts.load_resolution_rules` |
| TS resolution rules | `scripts.export_ts_resolution_rules` | `scripts.load_ts_resolution_rules` |

All follow the same `--out`/dry-run-by-default/`--commit` conventions as the combined
scripts above.
