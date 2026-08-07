-- NorthStar normalization reviewed-rule delta
-- Generated deterministically from immutable database versions.
-- Baseline: ts-review-20260806T133621914615Z
-- Target: ts-review-20260806T170328350936Z
-- Base catalog: ts-translation-v4
-- Delta definitions: 16
-- Target overrides: 67
-- Target SHA-256: 4c2bc7da0d20cdb8473e5e02810dc0864515d1946dc1a365536b008bb27d223b
-- Apply only to local, CI, or explicitly approved environments; never production by default.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_rules$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260806T133621914615Z';
    target_version CONSTANT text := 'ts-review-20260806T170328350936Z';
    expected_base_version CONSTANT text := 'ts-translation-v4';
    expected_activation_note CONSTANT text := 'Approved final general manufacturer rules for Bravo review cases; four ambiguous Brands remain manual';
    expected_activated_at CONSTANT timestamptz := '2026-08-06T17:03:28.356197+00:00';
    delta CONSTANT jsonb := $northstar_delta${"MFE-09F0AA8BA8649F":{"base_behavior":"use_entity","canonical_name":"Chevrolet","change_note":"Approved Chevrolet prefix including joined engine/model suffix forms at Brand start","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"approved_compact_prefix","reviewed_examples":["CHEVROLET","CHEVROLET 469 CHEVY II","CHEVROLET CAMARO","CHEVROLET CAPTIVA","CHEVROLET CHEVELLE MALIB","CHEVROLET CHEVY II 11837","CHEVROLET CORVETTE","CHEVROLET IMPALA","CHEVROLET KL1G","CHEVROLET KL1T","CHEVROLET KLAC","CHEVROLET MATZ SE","CHEVROLET VAN","CHEVROLETV8 BEL AIR CAB"],"source_field":"brand","source_term":"CHEVROLET"},"MFE-5E68668955A599":{"base_behavior":"use_entity","canonical_name":"Land Rover","change_note":"Approved Land Rover prefix including joined registry suffix forms at Brand start","entity_id":"MFE-5E68668955A599","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"approved_compact_prefix","reviewed_examples":["LAND ROVER DISCOVERY","LAND ROVERLV"],"source_field":"brand","source_term":"LAND ROVER"},"MFE-ADRIA-MOBIL-CONVERTER":{"base_behavior":"use_base_manufacturer","canonical_name":"Adria","change_note":"Use the explicit Mercedes-Benz base manufacturer and retain Adria as converter","entity_id":"MFE-ADRIA-MOBIL-CONVERTER","entity_role":"bodybuilder_converter","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"ADRIA MOBIL"},"MFE-AUTOMOBILES-PEUGEOT":{"base_behavior":"require_evidence_review","canonical_name":"Automobiles Peugeot","change_note":"Brand-confirmed Peugeot child mapping for the reviewed legal manufacturer","entity_id":"MFE-AUTOMOBILES-PEUGEOT","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"PEUGEOT":"Peugeot"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"AUTOMOBILES PEUGEOT"},"MFE-BRAND-BENTLEY":{"base_behavior":"use_entity","canonical_name":"Bentley","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-BENTLEY","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["BENTLEY CONTINENTAL"],"source_field":"brand","source_term":"BENTLEY"},"MFE-BRAND-DAEWOO":{"base_behavior":"use_entity","canonical_name":"Daewoo","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-DAEWOO","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["DAEWOO LANOS"],"source_field":"brand","source_term":"DAEWOO"},"MFE-BRAND-HUMMER":{"base_behavior":"use_entity","canonical_name":"Hummer","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-HUMMER","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["HUMMER H3G"],"source_field":"brand","source_term":"HUMMER"},"MFE-BRAND-LADA":{"base_behavior":"use_entity","canonical_name":"Lada","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-LADA","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["LADA NIVA 2121-1,6 M5"],"source_field":"brand","source_term":"LADA"},"MFE-BRAND-MG-COMPACT":{"base_behavior":"use_entity","canonical_name":"MG","change_note":"Approved compact-prefix handling for MG forms with spaces or joined model letters","entity_id":"MFE-BRAND-MG-COMPACT","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"approved_compact_prefix","reviewed_examples":["MG TF","M G B BMC 1800"],"source_field":"brand","source_term":"MG"},"MFE-BRAND-MORRIS":{"base_behavior":"use_entity","canonical_name":"Morris","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-MORRIS","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["MORRIS MINOR 1000"],"source_field":"brand","source_term":"MORRIS"},"MFE-BRAND-RANGE-ROVER":{"base_behavior":"use_entity","canonical_name":"Land Rover","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-RANGE-ROVER","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["RANGE ROVER VOGUE SE"],"source_field":"brand","source_term":"RANGE ROVER"},"MFE-BRAND-SSANGYONG":{"base_behavior":"use_entity","canonical_name":"SsangYong","change_note":"Approved complete Brand-prefix parent from reviewed Bravo passenger-car evidence","entity_id":"MFE-BRAND-SSANGYONG","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["SSANGYONG"],"source_field":"brand","source_term":"SSANGYONG"},"MFE-FCA-ITALY-BRAND":{"base_behavior":"require_evidence_review","canonical_name":"FCA Italy","change_note":"Brand-confirmed Fiat child mapping without assigning every FCA Italy vehicle to Fiat","entity_id":"MFE-FCA-ITALY-BRAND","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"FIAT":"Fiat"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"FCA ITALY"},"MFE-JAGUAR-LAND-ROVER":{"base_behavior":"require_evidence_review","canonical_name":"Jaguar Land Rover","change_note":"Brand-confirmed child mapping tolerant of TS-concatenated legal-name addresses","entity_id":"MFE-JAGUAR-LAND-ROVER","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"JAGUAR":"Jaguar","LAND ROVER":"Land Rover"},"match_type":"approved_compact_prefix","source_field":"manufacturer","source_term":"JAGUAR LAND ROVER LIMITED"},"MFE-MAGYAR-SUZUKI":{"base_behavior":"use_entity","canonical_name":"Suzuki","change_note":"Approved Suzuki legal manufacturer prefix from matching Brand and model evidence","entity_id":"MFE-MAGYAR-SUZUKI","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"MAGYAR SUZUKI CORPORATION"},"MFE-SAIC-MAXUS":{"base_behavior":"require_evidence_review","canonical_name":"SAIC Maxus","change_note":"Brand-confirmed Maxus child mapping for the reviewed SAIC Maxus legal manufacturer","entity_id":"MFE-SAIC-MAXUS","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"MAXUS":"Maxus"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"SAIC MAXUS AUTOMOTIVE"}}$northstar_delta$::jsonb;
    baseline_base text;
    baseline_overrides jsonb;
    current_latest text;
    existing_base text;
    existing_overrides jsonb;
    existing_note text;
    existing_activated_at timestamptz;
    expected_overrides jsonb;
BEGIN
    SELECT base_rule_version, overrides
    INTO baseline_base, baseline_overrides
    FROM core.translation_rule_versions
    WHERE version = baseline_version;

    IF baseline_overrides IS NULL THEN
        RAISE EXCEPTION 'Required baseline rule version % is missing', baseline_version;
    END IF;
    IF baseline_base <> expected_base_version THEN
        RAISE EXCEPTION 'Baseline catalog mismatch: expected %, found %',
            expected_base_version, baseline_base;
    END IF;

    expected_overrides := baseline_overrides || delta;

    SELECT base_rule_version, overrides, activation_note, activated_at
    INTO existing_base, existing_overrides, existing_note, existing_activated_at
    FROM core.translation_rule_versions
    WHERE version = target_version;

    IF existing_overrides IS NOT NULL THEN
        IF existing_base <> expected_base_version
           OR existing_overrides IS DISTINCT FROM expected_overrides
           OR existing_note IS DISTINCT FROM expected_activation_note
           OR existing_activated_at IS DISTINCT FROM expected_activated_at THEN
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
        expected_base_version,
        expected_overrides,
        expected_activation_note,
        expected_activated_at
    );
END
$northstar_rules$;

COMMIT;

WITH baseline AS (
    SELECT overrides
    FROM core.translation_rule_versions
    WHERE version = 'ts-review-20260806T133621914615Z'
), target AS (
    SELECT version, base_rule_version, overrides, activation_note, activated_at
    FROM core.translation_rule_versions
    WHERE version = 'ts-review-20260806T170328350936Z'
)
SELECT
    target.version,
    target.base_rule_version,
    (SELECT count(*) FROM jsonb_object_keys(target.overrides)) AS total_overrides,
    count(changed.key) AS exported_delta_definitions,
    target.activated_at
FROM target
CROSS JOIN baseline
CROSS JOIN LATERAL jsonb_each(target.overrides) AS changed
WHERE baseline.overrides->changed.key IS DISTINCT FROM changed.value
GROUP BY target.version, target.base_rule_version, target.overrides,
         target.activation_note, target.activated_at;
