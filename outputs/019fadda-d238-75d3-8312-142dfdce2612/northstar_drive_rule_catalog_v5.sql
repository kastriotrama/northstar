-- NorthStar reviewed drive-rule catalog activation
-- Baseline database version: ts-review-20260807T112656115381Z
-- Target database version: ts-review-20260807T142843182320Z
-- Target base catalog: ts-translation-v5
--
-- Rules supplied by the matching application version:
--   DRV-001 Mercedes-Benz + 4MATIC -> drive_type=awd
--   DRV-002 BMW + xDrive -> drive_type=awd
--   DRV-003 Audi + quattro -> drive_type=awd
--   DRV-004 Volkswagen + 4Motion/4 Motion -> drive_type=awd
--   DRV-008 Transportstyrelsen is_4wd=1 -> drive_type=awd
-- Transportstyrelsen is_4wd=0 remains unresolved and never guesses FWD or RWD.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_drive_rules$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260807T112656115381Z';
    target_version CONSTANT text := 'ts-review-20260807T142843182320Z';
    expected_baseline_catalog CONSTANT text := 'ts-translation-v4';
    target_catalog CONSTANT text := 'ts-translation-v5';
    target_note CONSTANT text := 'Approved TS AWD flag and manufacturer-scoped 4MATIC, xDrive, quattro, and 4Motion rules';
    target_activated_at CONSTANT timestamptz := '2026-08-07T14:28:43.182320+00:00';
    baseline_catalog text;
    baseline_overrides jsonb;
    current_latest text;
    existing_catalog text;
    existing_overrides jsonb;
    existing_note text;
    existing_activated_at timestamptz;
BEGIN
    SELECT base_rule_version, overrides
    INTO baseline_catalog, baseline_overrides
    FROM core.translation_rule_versions
    WHERE version = baseline_version;

    IF baseline_overrides IS NULL THEN
        RAISE EXCEPTION 'Required baseline rule version % is missing', baseline_version;
    END IF;
    IF baseline_catalog <> expected_baseline_catalog THEN
        RAISE EXCEPTION 'Baseline catalog mismatch: expected %, found %',
            expected_baseline_catalog, baseline_catalog;
    END IF;

    SELECT base_rule_version, overrides, activation_note, activated_at
    INTO existing_catalog, existing_overrides, existing_note, existing_activated_at
    FROM core.translation_rule_versions
    WHERE version = target_version;

    IF existing_overrides IS NOT NULL THEN
        IF existing_catalog <> target_catalog
           OR existing_overrides IS DISTINCT FROM baseline_overrides
           OR existing_note IS DISTINCT FROM target_note
           OR existing_activated_at IS DISTINCT FROM target_activated_at THEN
            RAISE EXCEPTION 'Target version % exists with conflicting content', target_version;
        END IF;
        RAISE NOTICE 'Target version % is already installed and verified', target_version;
        RETURN;
    END IF;

    SELECT version INTO current_latest
    FROM core.translation_rule_versions
    ORDER BY activated_at DESC, version DESC
    LIMIT 1;

    IF current_latest <> baseline_version THEN
        RAISE EXCEPTION 'Refusing activation: expected latest version %, found %',
            baseline_version, current_latest;
    END IF;

    INSERT INTO core.translation_rule_versions (
        version,
        base_rule_version,
        overrides,
        activation_note,
        activated_at
    ) VALUES (
        target_version,
        target_catalog,
        baseline_overrides,
        target_note,
        target_activated_at
    );
END
$northstar_drive_rules$;

COMMIT;

SELECT
    version,
    base_rule_version,
    (SELECT count(*) FROM jsonb_object_keys(overrides)) AS total_overrides,
    activation_note,
    activated_at
FROM core.translation_rule_versions
WHERE version = 'ts-review-20260807T142843182320Z';
