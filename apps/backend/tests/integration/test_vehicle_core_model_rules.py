"""Model rules: a car whose registry text names only the make gets its model from its siblings."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg import Connection

from ingestion.fuzzy_matching import VehicleCandidate
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_core_rules import (
    FAMILIES_BY_ID,
    apply_rules,
    check_model_fills,
    key_sql,
    learn_rules,
    retire_rule,
    store_rules,
)
from ingestion.vehicle_core_ts import backfill_vehicle_core
from ingestion.vehicle_model_guard import ModelGuard
from tests.integration.throwaway_database import throwaway_database
from tests.integration.vehicle_core_fixtures import (
    insert_ts_record,
    prepare_schema,
    project,
    volvo,
)

MODEL_FAMILIES = ("MOD-VV", "MOD-VIN", "MOD-TP", "MOD-VAR", "MOD-BR", "MOD-BT")
NO_CODES = {"variant": None, "version": None, "type_text": None}
CATALOG = (
    VehicleCandidate("v70", "Volvo", "V70 III (135)", model_aliases=("V70",), year_from=2007, year_to=2016),
    VehicleCandidate("xc70", "Volvo", "XC70 II (136)", model_aliases=("XC70",), year_from=2007, year_to=2016),
)
GUARD = ModelGuard(TecDocDryRunEvaluator(CATALOG))


class _Permissive:
    """A guard that objects to nothing: how fills were made before the guard existed."""

    def verdict(self, **_: object) -> None:
        return None


def _make_only(**overrides: object) -> dict[str, object]:
    """A TS row whose model text is empty, like the 2.37M cars these rules are for."""

    return volvo(model=None, **overrides)


@pytest.fixture(scope="module")
def db() -> Iterator[Connection]:
    with throwaway_database("vehicle_core_model_rules") as connection:
        prepare_schema(connection)
        # Ten V70s: the siblings every rule learns from.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1BW84S1F10000{index:02d}", plate=f"SIB{index:03d}"))
        # Only the VIN names the model: no variant, version or type code.
        insert_ts_record(connection, _make_only(vin="YV1BW84S1F2000001", plate="VIN001",
                                                variant=None, version=None, type_text=None))
        # Only variant + version name it: the VIN is pre-1981, not 17 characters.
        insert_ts_record(connection, _make_only(vin="000123", plate="VV0001"))
        # The VIN names a V70, but the car's own text names an XC70: the guard refuses.
        insert_ts_record(connection, _make_only(vin="YV1BW84S1F9000001", plate="GRD001",
                                                brand="VOLVO XC70 D5", **NO_CODES))
        # Nothing known names it: another descriptor and no codes.
        insert_ts_record(connection, _make_only(vin="YV1ZZ99S1F3000001", plate="UNK001",
                                                variant=None, version=None, type_text=None))
        # Three XC60s whose brand text names the model, and a car whose brand text
        # is the only thing naming it: another trim word, no codes, no VIN match.
        for index in range(3):
            insert_ts_record(connection, volvo(vin=f"YV1DZ40S1G40000{index:02d}", plate=f"XCS{index:03d}",
                                               brand="VOLVO XC60 D4", model="XC60",
                                               variant=None, version=None, type_text=None))
        insert_ts_record(connection, _make_only(vin="YV1QQ11S1H5000001", plate="BRT001",
                                                brand="VOLVO XC60 T6", variant=None, version=None,
                                                type_text=None))
        # Patterns. Three 740s put "740" in Volvo's vocabulary; a 1988 car registered
        # only by its type code shares no key with them.
        for index in range(3):
            insert_ts_record(connection, volvo(vin=f"YV1744A{index}", plate=f"SEV{index:03d}",
                                               model="740", vehicle_year=1988, **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1744B1", plate="PAT001", vehicle_year=1988,
                                                brand="VOLVO 744-883 GL", **NO_CODES))
        # Ten known 945-811s disagree among themselves, half of them with the
        # pattern (it says 940): no statistical rule, and no pattern rule either.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1945A{index}", plate=f"CON{index:03d}",
                                               brand="VOLVO 945-811 SE",
                                               model="V70" if index % 2 else "940", **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1945B1", plate="PAT002",
                                                brand="VOLVO 945-811 S", **NO_CODES))
        # Three known 245s say 240 and one stray says V70: one disagreement is tolerated.
        for index, model in enumerate(("240", "240", "240", "V70")):
            insert_ts_record(connection, volvo(vin=f"YV1245A{index}", plate=f"TOL{index:03d}",
                                               brand="VOLVO 245-883 GL", model=model, **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1245B1", plate="PAT003",
                                                brand="VOLVO 245-883 GL", **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        yield connection


def _model(connection: Connection, plate: str) -> tuple[object, object]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT model_family, field_sources ->> 'model_family' FROM core.vehicles WHERE plate = %s",
            (plate,),
        )
        row = cursor.fetchone()
    assert row is not None
    return row[0], row[1]


def test_the_fixture_cars_lack_a_model_and_the_siblings_have_one(db: Connection) -> None:
    assert _model(db, "SIB000")[0] == "V70"
    assert _model(db, "VIN001")[0] is None
    assert _model(db, "VV0001")[0] is None


def test_the_vin_descriptor_key_needs_a_full_vin(db: Connection) -> None:
    learned = learn_rules(db, FAMILIES_BY_ID["MOD-VIN"])

    assert [(rule.key_values, rule.value, rule.support) for rule in learned] == [
        (("Volvo", "YV1BW84S"), "V70", 10)
    ]


def test_a_model_family_is_never_applied_without_a_guard(db: Connection) -> None:
    with pytest.raises(ValueError, match="guard"):
        apply_rules(db, FAMILIES_BY_ID["MOD-VIN"])


def test_model_rules_fill_only_cars_their_key_names(db: Connection) -> None:
    filled, refused = {}, {}
    for name in MODEL_FAMILIES:
        family = FAMILIES_BY_ID[name]
        store_rules(db, family, learn_rules(db, family), learned_from=family.learned_from)
        applied = apply_rules(db, family, guard=GUARD)
        filled[name], refused[name] = applied.filled, applied.refused
    db.commit()

    # Variant + version is tried first, so it fills the car it names; the VIN
    # descriptor fills the one only its VIN names; nothing guesses the third.
    assert filled == {"MOD-VV": 1, "MOD-VIN": 1, "MOD-TP": 0, "MOD-VAR": 0, "MOD-BR": 0, "MOD-BT": 0}
    model, source = _model(db, "VIN001")
    assert model == "V70" and str(source).startswith("rule:MOD-VIN-")
    model, source = _model(db, "VV0001")
    assert model == "V70" and str(source).startswith("rule:MOD-VV-")
    assert _model(db, "UNK001") == (None, None)
    # A sibling's own model is never touched.
    assert _model(db, "SIB000") == ("V70", None)
    # The VIN rule would make the XC70 a V70; its own text says otherwise.
    assert refused["MOD-VIN"] == {"text_names_another_model": 1}
    assert _model(db, "GRD001") == (None, None)


def test_a_fill_made_before_the_guard_is_found_and_taken_back(db: Connection) -> None:
    # Filled the way it was before the guard existed.
    assert apply_rules(db, FAMILIES_BY_ID["MOD-VIN"], guard=_Permissive()).filled == 1
    db.commit()
    assert _model(db, "GRD001")[0] == "V70"

    dry = check_model_fills(db, GUARD)
    assert (dry.contradicted, dry.retracted) == ({"text_names_another_model": 1}, 0)
    assert dry.examples[0]["brand_text"] == "VOLVO XC70 D5" and dry.examples[0]["filled"] == "V70"
    assert _model(db, "GRD001")[0] == "V70"

    done = check_model_fills(db, GUARD, retract_contradicted=True)
    db.commit()
    assert done.retracted == 1
    assert _model(db, "GRD001") == (None, None)
    # The fills nothing contradicts stay.
    assert _model(db, "VIN001")[0] == "V70"


def test_a_model_rule_needs_its_familys_higher_bar(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-VIN"]

    assert family.min_support == 10 and family.min_agreement == 0.98
    assert learn_rules(db, family, min_support=11) == []


def test_the_guard_reads_what_ts_states_itself(db: Connection) -> None:
    from ingestion.vehicle_core_rules import (
        model_vocabulary,
        rule_id_for,
        stated_text_families,
        unanimous_vin_rule_eras,
    )

    stated = stated_text_families(db)
    # Three TS-named 245s say 240 and one says V70; the XC60s are named by their model text.
    assert stated[("Volvo", "brand", "VOLVO 245-883 GL")] == {"240": 3, "V70": 1}
    assert stated[("Volvo", "model", "XC60")] == {"XC60": 3}
    # A car TS left without a model is no TS-named sibling.
    assert ("Volvo", "brand", "VOLVO XC70 D5") not in stated
    # The VIN rule every TS-named V70 under its descriptor agreed with, and their years.
    v70 = rule_id_for("MOD-VIN", ("Volvo", "YV1BW84S"), "V70")
    assert unanimous_vin_rule_eras(db) == {v70: (2015, 2015)}
    # The names TS states itself, without or with the reviewed ones.
    ts_only = model_vocabulary(db, reviewed=False)["Volvo"]
    assert {"V70", "740", "XC60", "240", "940"} <= ts_only and "EX30" not in ts_only
    assert "EX30" in model_vocabulary(db)["Volvo"]


@pytest.mark.parametrize(
    ("text", "brand_text", "token"),
    [
        ("TOYOTA RAV4", "TOYOTA RAV4", "RAV4"),
        ("  bmw 320i touring ", "BMW 320I TOURING", "320I"),
        ("VOLVO S + V70", "VOLVO S + V70", "V70"),
        ("VOLVO 9 + 940 1998", "VOLVO 9 + 940 1998", "940"),
        ("MERCEDES-BENZ C 180 KOMP", "MERCEDES-BENZ C 180 KOMP", "C"),
        ("TOYOTA TOYOTA RAV4", "TOYOTA TOYOTA RAV4", None),
        ("POLESTAR", None, None),
        (None, None, None),
    ],
)
def test_brand_keys_read_the_model_out_of_registry_text(
    db: Connection, text: str | None, brand_text: str | None, token: str | None
) -> None:
    with db.cursor() as cursor:
        cursor.execute(
            f"SELECT {key_sql('brand_text', 'x')}, {key_sql('brand_token', 'x')} "
            "FROM (SELECT %s::text AS registry_brand_text) AS x",
            (text,),
        )
        assert cursor.fetchone() == (brand_text, token)


def test_the_brand_texts_word_after_the_make_names_the_model(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-BT"]
    learned = learn_rules(db, family, min_support=3)

    # The V70s' brand text is only the make, so they teach nothing.
    assert [(rule.key_values[1], rule.value) for rule in learned] == [("XC60", "XC60")]
    assert learn_rules(db, FAMILIES_BY_ID["MOD-BR"], min_support=3)[0].key_values[1] == "VOLVO XC60 D4"
    store_rules(db, family, learned, learned_from=family.learned_from)
    assert apply_rules(db, family, guard=GUARD).filled == 1
    db.commit()
    model, source = _model(db, "BRT001")
    assert model == "XC60" and str(source).startswith("rule:MOD-BT-")


def test_reviewed_patterns_read_a_type_code_and_yield_to_contradicting_cars(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-PAT"]
    learned = {rule.key_values[1]: rule for rule in learn_rules(db, family)}

    assert learned["744-883"].value == "740"
    assert learned["744-883"].agreement == 1.0  # no known 744-883 to check against
    assert "945-811" not in learned  # half of ten known cars say V70
    assert (learned["245-883"].value, learned["245-883"].agreement) == ("240", 0.75)
    store_rules(db, family, list(learned.values()), learned_from=family.learned_from)
    apply_rules(db, family, guard=GUARD)
    db.commit()
    model, source = _model(db, "PAT001")
    assert model == "740" and str(source).startswith("rule:MOD-PAT-")
    assert _model(db, "PAT002") == (None, None)
    assert _model(db, "PAT003")[0] == "240"


def test_a_pattern_rule_that_filled_all_its_cars_survives_the_next_learn(db: Connection) -> None:
    family = FAMILIES_BY_ID["MOD-PAT"]
    # PAT001, the only 744-883, was filled by the previous test: none is left empty.
    relearned = {rule.key_values[1]: rule for rule in learn_rules(db, family)}

    assert relearned["744-883"].value == "740"
    summary = store_rules(db, family, list(relearned.values()), learned_from=family.learned_from)
    db.commit()
    assert summary.retired == 0
    assert _model(db, "PAT001")[0] == "740"


def test_retiring_a_model_rule_takes_back_what_it_filled(db: Connection) -> None:
    _, source = _model(db, "VIN001")
    rule_id = str(source).removeprefix("rule:")

    assert retire_rule(db, rule_id) == 1
    db.commit()
    assert _model(db, "VIN001")[0] is None


def test_a_rule_keyed_on_a_number_fills_only_cars_of_its_learned_era() -> None:
    with throwaway_database("vehicle_core_model_era") as connection:
        prepare_schema(connection)
        # Ten recent cars whose brand text carries a chassis number, and the model.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1ERA{index:02d}", plate=f"ERA{index:03d}", vehicle_year=2002,
                                               build_month="200203", brand="VOLVO 220 T5", model="S60",
                                               **NO_CODES))
        # The same number on a car of that era, and on one decades older.
        insert_ts_record(connection, _make_only(vin="YV1ERAX1", plate="NEW001", vehicle_year=2003,
                                                build_month="200305", brand="VOLVO 220 D", **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1ERAX2", plate="OLD001", vehicle_year=1976,
                                                build_month="197605", brand="VOLVO 220 D", **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        family = FAMILIES_BY_ID["MOD-BT"]
        store_rules(connection, family, learn_rules(connection, family), learned_from=family.learned_from)
        applied = apply_rules(connection, family, guard=GUARD)
        connection.commit()

        assert applied.filled == 1 and applied.refused == {"outside_learned_era": 1}
        assert _model(connection, "NEW001")[0] == "S60"
        assert _model(connection, "OLD001") == (None, None)


def test_a_number_that_is_a_model_name_has_no_era() -> None:
    with throwaway_database("vehicle_core_model_name_era") as connection:
        prepare_schema(connection)
        # "940" is a Volvo model name: an old 940 is still a 940.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1NAM{index:02d}", plate=f"NAM{index:03d}", vehicle_year=1995,
                                               build_month="199503", brand="VOLVO 940 GL", model="940",
                                               **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1NAMX1", plate="OLD940", vehicle_year=1990,
                                                build_month="199005", brand="VOLVO 940 SE", **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        family = FAMILIES_BY_ID["MOD-BT"]
        store_rules(connection, family, learn_rules(connection, family), learned_from=family.learned_from)
        applied = apply_rules(connection, family, guard=GUARD)
        connection.commit()

        assert applied.filled == 1 and applied.refused == {}
        assert _model(connection, "OLD940")[0] == "940"


def test_a_rule_with_a_reviewed_era_keeps_only_fills_of_that_era(monkeypatch: pytest.MonkeyPatch) -> None:
    from ingestion.vehicle_core_rules import REVIEWED_ERAS_BY_RULE_ID

    with throwaway_database("vehicle_core_model_reviewed_era") as connection:
        prepare_schema(connection)
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1REV{index:02d}", plate=f"REV{index:03d}", vehicle_year=2012,
                                               build_month="201203", brand="VOLVO XC60 D4", model="XC60",
                                               **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1REVX1", plate="REVNEW", vehicle_year=2013,
                                                build_month="201305", brand="VOLVO XC60 D5", **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1REVX2", plate="REVOLD", vehicle_year=1975,
                                                build_month="197505", brand="VOLVO XC60 D5", **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        connection.commit()
        family = FAMILIES_BY_ID["MOD-BT"]
        learned = {rule.key_values[1]: rule for rule in learn_rules(connection, family)}
        store_rules(connection, family, list(learned.values()), learned_from=family.learned_from)
        # Filled before the rule's era was reviewed.
        assert apply_rules(connection, family, guard=_Permissive()).filled == 2
        connection.commit()

        monkeypatch.setitem(REVIEWED_ERAS_BY_RULE_ID, learned["XC60"].rule_id, ((2008, None),))
        dry = check_model_fills(connection, _Permissive())
        assert (dry.contradicted, dry.retracted) == ({"outside_reviewed_era": 1}, 0)
        assert check_model_fills(connection, _Permissive(), retract_contradicted=True).retracted == 1
        connection.commit()
        assert _model(connection, "REVOLD") == (None, None)
        assert _model(connection, "REVNEW")[0] == "XC60"
        # Applied again, the rule does not fill the car outside its era.
        applied = apply_rules(connection, family, guard=_Permissive())
        assert applied.filled == 0 and applied.refused == {"outside_reviewed_era": 1}


def test_the_guard_sees_the_cars_engine_code() -> None:
    with throwaway_database("vehicle_core_model_engine") as connection:
        prepare_schema(connection)
        # Ten known cars registered by the 940/960 type range, all 940s.
        for index in range(10):
            insert_ts_record(connection, volvo(vin=f"YV1TWO{index:02d}", plate=f"TWO{index:03d}", vehicle_year=1994,
                                               build_month="199403", brand="VOLVO 944-964 GL", model="940",
                                               **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1TWOX1", plate="TWO4CY", vehicle_year=1994,
                                                build_month="199405", brand="VOLVO 944-964 GLT", **NO_CODES))
        insert_ts_record(connection, _make_only(vin="YV1TWOX2", plate="TWO6CY", vehicle_year=1994,
                                                build_month="199405", brand="VOLVO 944-964 GLT", **NO_CODES))
        connection.commit()
        project(connection)
        backfill_vehicle_core(connection, min_free_bytes=None)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE core.vehicles SET engine_code = CASE plate WHEN 'TWO4CY' THEN 'B230FB' ELSE 'B6304F' END "
                "WHERE plate IN ('TWO4CY', 'TWO6CY')"
            )
        connection.commit()
        family = FAMILIES_BY_ID["MOD-BT"]
        store_rules(connection, family, learn_rules(connection, family), learned_from=family.learned_from)
        applied = apply_rules(connection, family, guard=GUARD)
        connection.commit()

        # The six-cylinder B6304F is a 960, whatever its type range taught the rule.
        assert applied.filled == 1 and applied.refused == {"engine_names_another_model": 1}
        assert _model(connection, "TWO4CY")[0] == "940"
        assert _model(connection, "TWO6CY") == (None, None)
        # A fill made before the check is found and taken back.
        assert apply_rules(connection, family, guard=_Permissive()).filled == 1
        connection.commit()
        assert check_model_fills(connection, GUARD).contradicted == {"engine_names_another_model": 1}
