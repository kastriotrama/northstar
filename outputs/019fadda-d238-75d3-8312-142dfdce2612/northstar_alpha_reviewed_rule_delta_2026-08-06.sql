-- NorthStar alpha normalization reviewed-rule delta
-- Generated: 2026-08-06
-- Scope: isolated local/CI test databases populated from the portable workbook.
-- Never run this file against production.
--
-- Prerequisite immutable workbook rule version:
--   ts-review-20260805T184254528647Z
-- Activated consolidated version:
--   ts-review-20260806T133621914615Z
--
-- This delta contains 33 added or extended definitions:
--   * Chevrolet complete Brand-prefix parent
--   * 15 additional reviewed Brand-prefix parents
--   * 9 legal-manufacturer prefix definitions
--   * PSA/Peugeot, FCA US/Jeep, and SAIC/MG evidence-gated child mappings
--   * Hymer and LMC converter/base-manufacturer definitions
--   * exact reviewed QUATTRO 42 -> Audi definition
--   * Renault manufacturer plus Adria converter compound-Brand definition
--
-- The script is idempotent. It verifies an existing target version byte-for-byte
-- and refuses to activate over an unexpected newer version.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_rules$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260805T184254528647Z';
    target_version CONSTANT text := 'ts-review-20260806T133621914615Z';
    expected_base_version CONSTANT text := 'ts-translation-v4';
    delta jsonb := $delta$
{"MFE-LMC-CONVERTER":{"kind":"manufacturer_entity","entity_id":"MFE-LMC-CONVERTER","match_type":"diacritic_insensitive_prefix","change_note":"Use explicit FCA Italy base manufacturer and retain LMC as converter","entity_role":"bodybuilder_converter","source_term":"LMC CARAVAN GMBH & CO","source_field":"manufacturer","base_behavior":"use_base_manufacturer","canonical_name":"LMC"},"MFE-09F0AA8BA8649F":{"kind":"manufacturer_entity","match_type":"whole_token_prefix","change_note":"Approved general Chevrolet Brand parent after reviewing all current complete-prefix examples","entity_role":"vehicle_manufacturer","source_term":"CHEVROLET","source_field":"brand","base_behavior":"use_entity","canonical_name":"Chevrolet","reviewed_examples":["CHEVROLET","CHEVROLET 469 CHEVY II","CHEVROLET CAMARO","CHEVROLET CAPTIVA","CHEVROLET CHEVELLE MALIB","CHEVROLET CHEVY II 11837","CHEVROLET CORVETTE","CHEVROLET IMPALA","CHEVROLET KL1G","CHEVROLET KL1T","CHEVROLET KLAC","CHEVROLET MATZ SE","CHEVROLET VAN"]},"MFE-1AF9829B01BD31":{"kind":"manufacturer_entity","entity_id":"MFE-1AF9829B01BD31","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"DKW","source_field":"brand","base_behavior":"use_entity","canonical_name":"DKW","reviewed_examples":["DKW AU 1000 LIMOUSINE 2D"]},"MFE-1C85D14143F5C3":{"kind":"manufacturer_entity","entity_id":"MFE-1C85D14143F5C3","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"DODGE","source_field":"brand","base_behavior":"use_entity","canonical_name":"Dodge","reviewed_examples":["DODGE DART GT HARD TOP"]},"MFE-22CA609E1DFBFF":{"kind":"manufacturer_entity","entity_id":"MFE-22CA609E1DFBFF","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"MAZDA MOTOR LOGISTICS","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Mazda"},"MFE-281EE783F1769E":{"kind":"manufacturer_entity","entity_id":"MFE-281EE783F1769E","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"RAMBLER","source_field":"brand","base_behavior":"use_entity","canonical_name":"Rambler","reviewed_examples":["RAMBLER JAVELIN 7079-7","RAMBLER JAVELIN 7179-7"]},"MFE-2BA064E09903C2":{"kind":"manufacturer_entity","entity_id":"MFE-2BA064E09903C2","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"PLYMOUTH","source_field":"brand","base_behavior":"use_entity","canonical_name":"Plymouth","reviewed_examples":["PLYMOUTH ROADRUNNER"]},"MFE-2C73FB9E71B9E8":{"kind":"manufacturer_entity","entity_id":"MFE-2C73FB9E71B9E8","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"AUSTIN","source_field":"brand","base_behavior":"use_entity","canonical_name":"Austin","reviewed_examples":["AUSTIN MAXI 1750","AUSTIN MINI 1000"]},"MFE-35DBF1CFD3DE18":{"kind":"manufacturer_entity","entity_id":"MFE-35DBF1CFD3DE18","match_type":"diacritic_insensitive_prefix","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_role":"corporate_group","source_term":"SAIC MOTOR CORPORATION","source_field":"manufacturer","base_behavior":"require_evidence_review","canonical_name":"SAIC Motor","marketed_brand_overrides":{"MG":"MG"}},"MFE-370E54AB886315":{"kind":"manufacturer_entity","entity_id":"MFE-370E54AB886315","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"MERCURY","source_field":"brand","base_behavior":"use_entity","canonical_name":"Mercury","reviewed_examples":["MERCURY COUGAR"]},"MFE-3EAD7FD5C78DA3":{"kind":"manufacturer_entity","entity_id":"MFE-3EAD7FD5C78DA3","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"SUZUKI","source_field":"brand","base_behavior":"use_entity","canonical_name":"Suzuki","reviewed_examples":["SUZUKI","SUZUKI 1,3 GL 5D KATALYT","SUZUKI GRAND VITARA V6AT","SUZUKI SX4 4WD MT"]},"MFE-4A9952E1711BCD":{"kind":"manufacturer_entity","entity_id":"MFE-4A9952E1711BCD","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"MITSUBISHI MOTORS CORPORATION","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Mitsubishi"},"MFE-5E68668955A599":{"kind":"manufacturer_entity","entity_id":"MFE-5E68668955A599","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"LAND ROVER","source_field":"brand","base_behavior":"use_entity","canonical_name":"Land Rover","reviewed_examples":["LAND ROVER DISCOVERY"]},"MFE-6C638702339AF8":{"kind":"manufacturer_entity","entity_id":"MFE-6C638702339AF8","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"VAZ","source_field":"brand","base_behavior":"use_entity","canonical_name":"VAZ","reviewed_examples":["VAZ NIVA 1600 2121 JET"]},"MFE-6CDEDE23F91163":{"kind":"manufacturer_entity","entity_id":"MFE-6CDEDE23F91163","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"POLESTAR PERFORMANCE AB","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Polestar"},"MFE-75ED3580C58AD8":{"kind":"manufacturer_entity","entity_id":"MFE-75ED3580C58AD8","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"AUTOMOBILES CITROEN","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Citroën"},"MFE-76642D2BFAF84D":{"kind":"manufacturer_entity","entity_id":"MFE-76642D2BFAF84D","match_type":"diacritic_insensitive_prefix","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_role":"corporate_group","source_term":"SAIC MOTOR EUROPE","source_field":"manufacturer","base_behavior":"require_evidence_review","canonical_name":"SAIC Motor","marketed_brand_overrides":{"MG":"MG"}},"MFE-7B56EB48EB08F0":{"kind":"manufacturer_entity","entity_id":"MFE-7B56EB48EB08F0","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"ALFA ROMEO","source_field":"brand","base_behavior":"use_entity","canonical_name":"Alfa Romeo","reviewed_examples":["ALFA ROMEO 1,6 TS","ALFA ROMEO SPIDER 2,0 TS"]},"MFE-7DEFBE67897D09":{"kind":"manufacturer_entity","entity_id":"MFE-7DEFBE67897D09","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"TRIUMPH","source_field":"brand","base_behavior":"use_entity","canonical_name":"Triumph","reviewed_examples":["TRIUMPH HERALD 1200"]},"MFE-887AA4A33831C7":{"kind":"manufacturer_entity","entity_id":"MFE-887AA4A33831C7","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"LINCOLN","source_field":"brand","base_behavior":"use_entity","canonical_name":"Lincoln","reviewed_examples":["LINCOLN PREMIERE"]},"MFE-8891A0D57C8483":{"kind":"manufacturer_entity","entity_id":"MFE-8891A0D57C8483","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"MAZDA MOTOR CORPORATION","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Mazda"},"MFE-8DB3252A955C99":{"kind":"manufacturer_entity","aliases":["P.S.A. AUTOMOBILES"],"entity_id":"MFE-8DB3252A955C99","match_type":"diacritic_insensitive_prefix","change_note":"Extended approved PSA child allow-list with explicit Peugeot Brand evidence","entity_role":"corporate_group","source_term":"PSA AUTOMOBILES","source_field":"manufacturer","base_behavior":"require_evidence_review","canonical_name":"PSA Automobiles","reviewed_examples":["PSA AUTOMOBILES SA"],"marketed_brand_overrides":{"CITROEN":"Citroën","PEUGEOT":"Peugeot","CITROËN":"Citroën"}},"MFE-9EDA852A511213":{"kind":"manufacturer_entity","entity_id":"MFE-9EDA852A511213","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"KIAMOTORSSLOVAKIA","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Kia"},"MFE-A4E56ADEBD732E":{"kind":"manufacturer_entity","entity_id":"MFE-A4E56ADEBD732E","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"BORGWARD","source_field":"brand","base_behavior":"use_entity","canonical_name":"Borgward","reviewed_examples":["BORGWARD ARABELLA"]},"MFE-AC51C21ECF3586":{"kind":"manufacturer_entity","entity_id":"MFE-AC51C21ECF3586","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"SUZUKI MOTOR CORPORATION","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Suzuki"},"MFE-BF32866A78BBD5":{"kind":"manufacturer_entity","entity_id":"MFE-BF32866A78BBD5","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"KIASLOVAKIAS.R.O.","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Kia"},"MFE-C0C61E456F7D25":{"kind":"manufacturer_entity","entity_id":"MFE-C0C61E456F7D25","match_type":"diacritic_insensitive_prefix","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_role":"corporate_group","source_term":"FCA US LLC","source_field":"manufacturer","base_behavior":"require_evidence_review","canonical_name":"FCA US","marketed_brand_overrides":{"JEEP":"Jeep"}},"MFE-E819113F3939DC":{"kind":"manufacturer_entity","entity_id":"MFE-E819113F3939DC","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"CADILLAC","source_field":"brand","base_behavior":"use_entity","canonical_name":"Cadillac","reviewed_examples":["CADILLAC DE VILLE","CADILLAC GMX322","CADILLAC HT"]},"MFE-F0186481EFB418":{"kind":"manufacturer_entity","entity_id":"MFE-F0186481EFB418","match_type":"whole_token_prefix","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_role":"vehicle_manufacturer","source_term":"OLDSMOBILE","source_field":"brand","base_behavior":"use_entity","canonical_name":"Oldsmobile","reviewed_examples":["OLDSMOBILE 342693","OLDSMOBILE CUTLASS","OLDSMOBILE NINETY-EIGHT"]},"MFE-FE2DD209A0DAB2":{"kind":"manufacturer_entity","entity_id":"MFE-FE2DD209A0DAB2","match_type":"diacritic_insensitive_prefix","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_role":"vehicle_manufacturer","source_term":"AUTOMOBILE DACIA S.A.","source_field":"manufacturer","base_behavior":"use_entity","canonical_name":"Dacia"},"MFE-QUATTRO42-AUDI":{"kind":"manufacturer_entity","entity_id":"MFE-QUATTRO42-AUDI","match_type":"exact","change_note":"Reviewed exact Quattro 42 exception supported by Audi R8 model and WUA VIN evidence","entity_role":"vehicle_manufacturer","source_term":"QUATTRO 42","source_field":"brand","base_behavior":"use_entity","canonical_name":"Audi","reviewed_examples":["QUATTRO 42"]},"MFE-HYMER-CONVERTER":{"kind":"manufacturer_entity","entity_id":"MFE-HYMER-CONVERTER","match_type":"diacritic_insensitive_prefix","change_note":"Use explicit Mercedes-Benz base manufacturer and retain Hymer as converter","entity_role":"bodybuilder_converter","source_term":"HYMER GMBH & CO. KG","source_field":"manufacturer","base_behavior":"use_base_manufacturer","canonical_name":"Hymer"}}
$delta$::jsonb;
    baseline_base text;
    baseline_overrides jsonb;
    current_latest text;
    existing_base text;
    existing_overrides jsonb;
    expected_overrides jsonb;
BEGIN
    delta := delta || jsonb_build_object(
        'MFE-RENAULT-ADRIA',
        jsonb_build_object(
            'entity_id', 'MFE-RENAULT-ADRIA',
            'kind', 'manufacturer_entity',
            'source_field', 'brand',
            'source_term', 'RENAULT ADRIA MOBIL',
            'canonical_name', 'Adria',
            'entity_role', 'bodybuilder_converter',
            'base_behavior', 'use_base_manufacturer',
            'fallback_manufacturer', 'Renault',
            'match_type', 'whole_token_prefix',
            'reviewed_examples', jsonb_build_array('RENAULT ADRIA MOBIL'),
            'change_note', 'Use Renault from compound Brand as manufacturer and retain Adria as converter'
        )
    );

    SELECT base_rule_version, overrides
    INTO baseline_base, baseline_overrides
    FROM core.translation_rule_versions
    WHERE version = baseline_version;

    IF baseline_overrides IS NULL THEN
        RAISE EXCEPTION 'Required baseline rule version % is missing', baseline_version;
    END IF;

    IF baseline_base <> expected_base_version THEN
        RAISE EXCEPTION 'Baseline application catalog mismatch: expected %, found %',
            expected_base_version, baseline_base;
    END IF;

    expected_overrides := baseline_overrides || delta;

    SELECT base_rule_version, overrides
    INTO existing_base, existing_overrides
    FROM core.translation_rule_versions
    WHERE version = target_version;

    IF existing_overrides IS NOT NULL THEN
        IF existing_base <> expected_base_version
           OR existing_overrides IS DISTINCT FROM expected_overrides THEN
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
        RAISE EXCEPTION
            'Refusing activation: expected latest version %, found %',
            baseline_version, current_latest;
    END IF;

    INSERT INTO core.translation_rule_versions (
        version,
        base_rule_version,
        overrides,
        activation_note
    ) VALUES (
        target_version,
        expected_base_version,
        expected_overrides,
        'Activate reviewed alpha manufacturer rule delta from portable SQL bundle'
    );
END
$northstar_rules$;

COMMIT;

-- Verification: target version, total overrides, and the 33 exported definitions.
WITH baseline AS (
    SELECT overrides
    FROM core.translation_rule_versions
    WHERE version = 'ts-review-20260805T184254528647Z'
), target AS (
    SELECT version, base_rule_version, overrides, activated_at
    FROM core.translation_rule_versions
    WHERE version = 'ts-review-20260806T133621914615Z'
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
GROUP BY target.version, target.base_rule_version, target.overrides, target.activated_at;

-- After installation, reprocess the imported alpha batch through the API/CLI.
-- Verified isolated result after all exported definitions:
--   total=1000, resolved=70, provisional=926, review_required=4, failed=0
-- The four intentionally unresolved records are Great Wall/ORA, Rapido,
-- Volkswagen California, and Volkswagen Multivan.
