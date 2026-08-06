-- NorthStar normalization reviewed-rule delta
-- Generated deterministically from immutable database versions.
-- Baseline: ts-review-20260805T184254528647Z
-- Target: ts-review-20260806T133621914615Z
-- Base catalog: ts-translation-v4
-- Delta definitions: 33
-- Target overrides: 53
-- Target SHA-256: 56298ae5110396e36d9860be4201a6554110f8c7cf7470e5011d81644c911185
-- Apply only to local, CI, or explicitly approved environments; never production by default.

\set ON_ERROR_STOP on

BEGIN;

LOCK TABLE core.translation_rule_versions IN SHARE ROW EXCLUSIVE MODE;

DO $northstar_rules$
DECLARE
    baseline_version CONSTANT text := 'ts-review-20260805T184254528647Z';
    target_version CONSTANT text := 'ts-review-20260806T133621914615Z';
    expected_base_version CONSTANT text := 'ts-translation-v4';
    expected_activation_note CONSTANT text := 'Activate reviewed alpha manufacturer rule delta from portable SQL bundle';
    expected_activated_at CONSTANT timestamptz := '2026-08-06T15:09:25.423251+00:00';
    delta CONSTANT jsonb := $northstar_delta${"MFE-09F0AA8BA8649F":{"base_behavior":"use_entity","canonical_name":"Chevrolet","change_note":"Approved general Chevrolet Brand parent after reviewing all current complete-prefix examples","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["CHEVROLET","CHEVROLET 469 CHEVY II","CHEVROLET CAMARO","CHEVROLET CAPTIVA","CHEVROLET CHEVELLE MALIB","CHEVROLET CHEVY II 11837","CHEVROLET CORVETTE","CHEVROLET IMPALA","CHEVROLET KL1G","CHEVROLET KL1T","CHEVROLET KLAC","CHEVROLET MATZ SE","CHEVROLET VAN"],"source_field":"brand","source_term":"CHEVROLET"},"MFE-1AF9829B01BD31":{"base_behavior":"use_entity","canonical_name":"DKW","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-1AF9829B01BD31","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["DKW AU 1000 LIMOUSINE 2D"],"source_field":"brand","source_term":"DKW"},"MFE-1C85D14143F5C3":{"base_behavior":"use_entity","canonical_name":"Dodge","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-1C85D14143F5C3","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["DODGE DART GT HARD TOP"],"source_field":"brand","source_term":"DODGE"},"MFE-22CA609E1DFBFF":{"base_behavior":"use_entity","canonical_name":"Mazda","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-22CA609E1DFBFF","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"MAZDA MOTOR LOGISTICS"},"MFE-281EE783F1769E":{"base_behavior":"use_entity","canonical_name":"Rambler","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-281EE783F1769E","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["RAMBLER JAVELIN 7079-7","RAMBLER JAVELIN 7179-7"],"source_field":"brand","source_term":"RAMBLER"},"MFE-2BA064E09903C2":{"base_behavior":"use_entity","canonical_name":"Plymouth","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-2BA064E09903C2","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["PLYMOUTH ROADRUNNER"],"source_field":"brand","source_term":"PLYMOUTH"},"MFE-2C73FB9E71B9E8":{"base_behavior":"use_entity","canonical_name":"Austin","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-2C73FB9E71B9E8","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["AUSTIN MAXI 1750","AUSTIN MINI 1000"],"source_field":"brand","source_term":"AUSTIN"},"MFE-35DBF1CFD3DE18":{"base_behavior":"require_evidence_review","canonical_name":"SAIC Motor","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_id":"MFE-35DBF1CFD3DE18","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"MG":"MG"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"SAIC MOTOR CORPORATION"},"MFE-370E54AB886315":{"base_behavior":"use_entity","canonical_name":"Mercury","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-370E54AB886315","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["MERCURY COUGAR"],"source_field":"brand","source_term":"MERCURY"},"MFE-3EAD7FD5C78DA3":{"base_behavior":"use_entity","canonical_name":"Suzuki","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-3EAD7FD5C78DA3","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["SUZUKI","SUZUKI 1,3 GL 5D KATALYT","SUZUKI GRAND VITARA V6AT","SUZUKI SX4 4WD MT"],"source_field":"brand","source_term":"SUZUKI"},"MFE-4A9952E1711BCD":{"base_behavior":"use_entity","canonical_name":"Mitsubishi","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-4A9952E1711BCD","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"MITSUBISHI MOTORS CORPORATION"},"MFE-5E68668955A599":{"base_behavior":"use_entity","canonical_name":"Land Rover","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-5E68668955A599","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["LAND ROVER DISCOVERY"],"source_field":"brand","source_term":"LAND ROVER"},"MFE-6C638702339AF8":{"base_behavior":"use_entity","canonical_name":"VAZ","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-6C638702339AF8","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["VAZ NIVA 1600 2121 JET"],"source_field":"brand","source_term":"VAZ"},"MFE-6CDEDE23F91163":{"base_behavior":"use_entity","canonical_name":"Polestar","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-6CDEDE23F91163","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"POLESTAR PERFORMANCE AB"},"MFE-75ED3580C58AD8":{"base_behavior":"use_entity","canonical_name":"Citroën","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-75ED3580C58AD8","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"AUTOMOBILES CITROEN"},"MFE-76642D2BFAF84D":{"base_behavior":"require_evidence_review","canonical_name":"SAIC Motor","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_id":"MFE-76642D2BFAF84D","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"MG":"MG"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"SAIC MOTOR EUROPE"},"MFE-7B56EB48EB08F0":{"base_behavior":"use_entity","canonical_name":"Alfa Romeo","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-7B56EB48EB08F0","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["ALFA ROMEO 1,6 TS","ALFA ROMEO SPIDER 2,0 TS"],"source_field":"brand","source_term":"ALFA ROMEO"},"MFE-7DEFBE67897D09":{"base_behavior":"use_entity","canonical_name":"Triumph","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-7DEFBE67897D09","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["TRIUMPH HERALD 1200"],"source_field":"brand","source_term":"TRIUMPH"},"MFE-887AA4A33831C7":{"base_behavior":"use_entity","canonical_name":"Lincoln","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-887AA4A33831C7","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["LINCOLN PREMIERE"],"source_field":"brand","source_term":"LINCOLN"},"MFE-8891A0D57C8483":{"base_behavior":"use_entity","canonical_name":"Mazda","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-8891A0D57C8483","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"MAZDA MOTOR CORPORATION"},"MFE-8DB3252A955C99":{"aliases":["P.S.A. AUTOMOBILES"],"base_behavior":"require_evidence_review","canonical_name":"PSA Automobiles","change_note":"Extended approved PSA child allow-list with explicit Peugeot Brand evidence","entity_id":"MFE-8DB3252A955C99","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"CITROEN":"Citroën","CITROËN":"Citroën","PEUGEOT":"Peugeot"},"match_type":"diacritic_insensitive_prefix","reviewed_examples":["PSA AUTOMOBILES SA"],"source_field":"manufacturer","source_term":"PSA AUTOMOBILES"},"MFE-9EDA852A511213":{"base_behavior":"use_entity","canonical_name":"Kia","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-9EDA852A511213","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"KIAMOTORSSLOVAKIA"},"MFE-A4E56ADEBD732E":{"base_behavior":"use_entity","canonical_name":"Borgward","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-A4E56ADEBD732E","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["BORGWARD ARABELLA"],"source_field":"brand","source_term":"BORGWARD"},"MFE-AC51C21ECF3586":{"base_behavior":"use_entity","canonical_name":"Suzuki","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-AC51C21ECF3586","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"SUZUKI MOTOR CORPORATION"},"MFE-BF32866A78BBD5":{"base_behavior":"use_entity","canonical_name":"Kia","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-BF32866A78BBD5","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"KIASLOVAKIAS.R.O."},"MFE-C0C61E456F7D25":{"base_behavior":"require_evidence_review","canonical_name":"FCA US","change_note":"Approved corporate child mapping only with explicit reviewed Brand evidence","entity_id":"MFE-C0C61E456F7D25","entity_role":"corporate_group","kind":"manufacturer_entity","marketed_brand_overrides":{"JEEP":"Jeep"},"match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"FCA US LLC"},"MFE-E819113F3939DC":{"base_behavior":"use_entity","canonical_name":"Cadillac","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-E819113F3939DC","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["CADILLAC DE VILLE","CADILLAC GMX322","CADILLAC HT"],"source_field":"brand","source_term":"CADILLAC"},"MFE-F0186481EFB418":{"base_behavior":"use_entity","canonical_name":"Oldsmobile","change_note":"Approved complete Brand-prefix parent from reviewed alpha examples","entity_id":"MFE-F0186481EFB418","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["OLDSMOBILE 342693","OLDSMOBILE CUTLASS","OLDSMOBILE NINETY-EIGHT"],"source_field":"brand","source_term":"OLDSMOBILE"},"MFE-FE2DD209A0DAB2":{"base_behavior":"use_entity","canonical_name":"Dacia","change_note":"Approved reviewed legal-manufacturer prefix from matching Brand evidence","entity_id":"MFE-FE2DD209A0DAB2","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"AUTOMOBILE DACIA S.A."},"MFE-HYMER-CONVERTER":{"base_behavior":"use_base_manufacturer","canonical_name":"Hymer","change_note":"Use explicit Mercedes-Benz base manufacturer and retain Hymer as converter","entity_id":"MFE-HYMER-CONVERTER","entity_role":"bodybuilder_converter","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"HYMER GMBH & CO. KG"},"MFE-LMC-CONVERTER":{"base_behavior":"use_base_manufacturer","canonical_name":"LMC","change_note":"Use explicit FCA Italy base manufacturer and retain LMC as converter","entity_id":"MFE-LMC-CONVERTER","entity_role":"bodybuilder_converter","kind":"manufacturer_entity","match_type":"diacritic_insensitive_prefix","source_field":"manufacturer","source_term":"LMC CARAVAN GMBH & CO"},"MFE-QUATTRO42-AUDI":{"base_behavior":"use_entity","canonical_name":"Audi","change_note":"Reviewed exact Quattro 42 exception supported by Audi R8 model and WUA VIN evidence","entity_id":"MFE-QUATTRO42-AUDI","entity_role":"vehicle_manufacturer","kind":"manufacturer_entity","match_type":"exact","reviewed_examples":["QUATTRO 42"],"source_field":"brand","source_term":"QUATTRO 42"},"MFE-RENAULT-ADRIA":{"base_behavior":"use_base_manufacturer","canonical_name":"Adria","change_note":"Use Renault from compound Brand as manufacturer and retain Adria as converter","entity_id":"MFE-RENAULT-ADRIA","entity_role":"bodybuilder_converter","fallback_manufacturer":"Renault","kind":"manufacturer_entity","match_type":"whole_token_prefix","reviewed_examples":["RENAULT ADRIA MOBIL"],"source_field":"brand","source_term":"RENAULT ADRIA MOBIL"}}$northstar_delta$::jsonb;
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
    WHERE version = 'ts-review-20260805T184254528647Z'
), target AS (
    SELECT version, base_rule_version, overrides, activation_note, activated_at
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
GROUP BY target.version, target.base_rule_version, target.overrides,
         target.activation_note, target.activated_at;
