# Reviewed rule SQL workflow

## Source of truth

`core.translation_rule_versions` is the source of truth for reviewed normalization rules.
Every activation from the web workbench creates a new immutable row that inherits the prior
overrides. SQL files are generated deployment artifacts; they are never edited to define rules.

Application catalog upgrades, such as the reviewed Drive category introduced by
`ts-translation-v5`, use a separate checked SQL activation artifact. That artifact advances the
immutable database version to the matching application catalog while preserving every activated
override. Apply the application commit and its catalog-activation SQL together; either one alone
is intentionally insufficient.

This separation keeps rule review understandable:

1. inspect current data and create drafts in `/normalization-review`;
2. activate the reviewed drafts with an evidence note;
3. export the new immutable version relative to the version already deployed elsewhere;
4. validate and share the generated SQL;
5. apply it to another approved environment and re-import the latest source batch.

## Export the latest active rules

Use the immutable version currently installed in the destination environment as the baseline.
Omitting `--target-version` intentionally selects the latest active version in the source database:

```bash
northstar-ingest export-rule-delta \
  --baseline-version ts-review-20260805T184254528647Z \
  --output outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_latest_reviewed_rule_delta.sql
```

For a historical or release-specific export, provide `--target-version` explicitly. The command
prints baseline, target, catalog, definition count, total override count, SHA-256, and output path
as JSON for CI or release logs.

## Safety contract

The generator:

- reads both versions from immutable PostgreSQL rows;
- includes only added or changed definitions;
- rejects a target that removes inherited definitions;
- rejects different base application catalogs;
- emits canonical, deterministically ordered JSON and a SHA-256 checksum;
- preserves the exact target version, activation note, and activation timestamp;
- makes the SQL idempotent when identical content is already installed;
- rejects a conflicting target version or an unexpected newer active version;
- locks the rule-version table while applying the transaction;
- verifies the installed total and delta counts after commit.

Never run a generated rule delta against production without the normal deployment approval.

## Apply and validate elsewhere

Apply the generated artifact with `psql` in an isolated or approved target environment:

```bash
psql "$DATABASE_URL" \
  --set ON_ERROR_STOP=1 \
  --file outputs/019fadda-d238-75d3-8312-142dfdce2612/northstar_latest_reviewed_rule_delta.sql
```

Then open `/normalization-review`, select the newest imported source batch, and choose
**Re-import current batch**. Compare the before/after counts and inspect every remaining review
reason. Rule installation does not rewrite an old result batch; re-import creates a new batch with
the new immutable rule version and leaves the prior evidence untouched.

## Keeping the artifact current

Generate a fresh SQL file after every approved activation. The database version—not the filename
or current dataset—determines its content. When newer TS data exposes missing terminology, review
that evidence in the web workbench, activate a new version, regenerate the delta, and re-import the
latest cohort. This prevents unreviewed source values from silently becoming rules.
