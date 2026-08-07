-- NorthStar reviewed model-family phase-two catalog activation
-- Baseline database version: ts-review-20260807T145505493277Z
-- Target database version: ts-review-20260807T150613695269Z
-- Target base catalog: ts-translation-v7
--
-- The matching application adds MOD-101..MOD-202 for reviewed clean families,
-- manufacturer-scoped composite decomposition, and spelling normalization.
-- Kia SL/ED and other unverified internal codes deliberately remain candidates.
-- No TecDoc inference is included in this catalog.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_model_rules$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260807T145505493277Z';
    target_version CONSTANT text := 'ts-review-20260807T150613695269Z';
    expected_baseline_catalog CONSTANT text := 'ts-translation-v6';
    target_catalog CONSTANT text := 'ts-translation-v7';
    target_note CONSTANT text := 'Approved model-family phase two MOD-101 through MOD-202 without TecDoc inference';
    target_activated_at CONSTANT timestamptz := '2026-08-07T15:06:13.695269+00:00';
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
$northstar_model_rules$;

COMMIT;

SELECT
    version,
    base_rule_version,
    (SELECT count(*) FROM jsonb_object_keys(overrides)) AS total_overrides,
    activation_note,
    activated_at
FROM core.translation_rule_versions
WHERE version = 'ts-review-20260807T150613695269Z';
