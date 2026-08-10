-- NorthStar Transportstyrelsen special-vehicle safety policy activation
-- Baseline database version: ts-review-20260807T150613695269Z
-- Target database version: ts-review-20260810T143500000000Z
-- Target application catalog: ts-translation-v7
--
-- This immutable override makes the T12/special-purpose safety behavior
-- replayable from SQL. The matching application version reads this policy;
-- older application versions must not activate this target.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_special_vehicle_policy$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260807T150613695269Z';
    target_version CONSTANT text := 'ts-review-20260810T143500000000Z';
    target_catalog CONSTANT text := 'ts-translation-v7';
    target_note CONSTANT text := 'Approved TS special-modified and special-purpose parts-matching safety policy';
    target_activated_at CONSTANT timestamptz := '2026-08-10T14:35:00+02:00';
    policy_definition CONSTANT jsonb := jsonb_build_object(
        'kind', 'special_vehicle_policy',
        'rule_id', 'TS-SPECIAL-VEHICLE-V1',
        'special_modified_text_codes', jsonb_build_array('T12A', 'T12B', 'T12BF', 'T12C'),
        'special_body_code_flags', jsonb_build_object(
            '06', 'taxi',
            '75', 'fire_rescue_vehicle',
            '88', 'customs_vehicle',
            '89', 'coast_guard_vehicle',
            '91', 'recovery_vehicle',
            '93', 'police_vehicle',
            '95', 'fire_rescue_vehicle',
            '99', 'ambulance',
            'SA', 'motor_caravan',
            'SB', 'armoured_vehicle',
            'SC', 'ambulance',
            'SD', 'hearse',
            'SG', 'other_special_purpose',
            'SH', 'wheelchair_accessible'
        ),
        'manufacturer_group', 'Special Modified',
        'parts_matching_policy', 'excluded',
        'tecdoc_match_policy', 'exclude',
        'other_special_parts_matching_policy', 'manual_review',
        'change_note', 'Approved TS text-code and special-purpose safety behavior'
    );
    baseline_catalog text;
    baseline_overrides jsonb;
    current_latest text;
    existing_catalog text;
    existing_overrides jsonb;
    existing_note text;
    existing_activated_at timestamptz;
    target_overrides jsonb;
BEGIN
    SELECT base_rule_version, overrides
    INTO baseline_catalog, baseline_overrides
    FROM core.translation_rule_versions
    WHERE version = baseline_version;

    IF baseline_overrides IS NULL THEN
        RAISE EXCEPTION 'Required baseline rule version % is missing', baseline_version;
    END IF;
    IF baseline_catalog <> target_catalog THEN
        RAISE EXCEPTION 'Baseline catalog mismatch: expected %, found %',
            target_catalog, baseline_catalog;
    END IF;

    target_overrides := baseline_overrides || jsonb_build_object(
        'TS-SPECIAL-VEHICLE-V1', policy_definition
    );

    SELECT base_rule_version, overrides, activation_note, activated_at
    INTO existing_catalog, existing_overrides, existing_note, existing_activated_at
    FROM core.translation_rule_versions
    WHERE version = target_version;

    IF existing_overrides IS NOT NULL THEN
        IF existing_catalog <> target_catalog
           OR existing_overrides IS DISTINCT FROM target_overrides
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
        target_overrides,
        target_note,
        target_activated_at
    );
END
$northstar_special_vehicle_policy$;

COMMIT;

SELECT
    version,
    base_rule_version,
    (SELECT count(*) FROM jsonb_object_keys(overrides)) AS total_overrides,
    overrides->'TS-SPECIAL-VEHICLE-V1' AS special_vehicle_policy,
    activation_note,
    activated_at
FROM core.translation_rule_versions
WHERE version = 'ts-review-20260810T143500000000Z';
