import pytest

from ingestion.fuzzy_matching import ManufacturerCandidateIndex, VehicleCandidate
from ingestion.match_run_service import MatchSourceRecord
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator


def catalog():
    return (
        VehicleCandidate("1", "VOLVO", "V70 II", model_aliases=("V70",), year_from=2000, year_to=2007),
        VehicleCandidate("2", "VOLVO", "V70 III", model_aliases=("V70",), year_from=2008, year_to=2016),
    )


def test_multiple_generations_recover_family_not_arbitrary_generation():
    assert ManufacturerCandidateIndex(catalog()).recover_model_from_evidence(
        "VOLVO", {"brand": "VOLVO S + V70"}
    ) == ("V70", "brand")


def test_recovered_family_without_distinguishing_evidence_stays_unresolved():
    result = TecDocDryRunEvaluator(catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VOLVO"}, "source_evidence": {"brand": "VOLVO V70"},
    }))
    assert result.terminal == "review_required"
    assert "model_evidence_missing" not in result.reason_codes
    assert "route:candidate_margin_below_gate" in result.reason_codes


def test_recovered_family_can_use_existing_year_gate():
    result = TecDocDryRunEvaluator(catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VOLVO", "production_year": 2010},
        "source_evidence": {"brand": "VOLVO V70"},
    }))
    assert result.top_candidate_reference == "2"
    assert result.terminal == "resolved"


def test_shared_trim_cannot_recover_unrelated_family():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Ford", "Focus", model_aliases=("Sport",)),
        VehicleCandidate("2", "Ford", "Fiesta", model_aliases=("Sport",)),
    ))
    assert index.recover_model_from_evidence("Ford", {"brand": "Ford Sport"}) is None


def test_unrelated_longest_evidence_cannot_be_discarded():
    index = ManufacturerCandidateIndex((*catalog(),
        VehicleCandidate("3", "VOLVO", "S60", model_aliases=("ABC",)),
    ))
    assert index.recover_model_from_evidence("VOLVO", {"brand": "V70 ABC"}) is None


def saab_catalog():
    return (
        VehicleCandidate("3", "SAAB", "9-3 (YS3D)", model_aliases=("9-3",), year_from=1998, year_to=2003),
        VehicleCandidate("4", "SAAB", "9-3 (YS3F)", model_aliases=("9-3",), year_from=2002, year_to=2015),
        VehicleCandidate("5", "SAAB", "9-5 (YS3E)", model_aliases=("9-5",), year_from=1997, year_to=2009),
        VehicleCandidate("6", "SAAB", "9-5 (YS3G)", model_aliases=("9-5",), year_from=2010, year_to=2012),
    )


@pytest.mark.parametrize("name,label", [("9-3", "9 3"), ("9-5", "9 5"), ("9‑3", "9 3"), ("9 - 5", "9 5")])
@pytest.mark.parametrize("field", ["brand", "model"])
def test_hyphenated_saab_name_recovers_catalog_family(name, label, field):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence(
        "Saab", {field: f"SAAB {name} LINEAR SPORTCOM"}
    ) == (label, field)


@pytest.mark.parametrize("text", ["19-3", "9-30", "9-3X", "9/3", "9.3", "9 3", "93", "95", "A9-3", "9-3A"])
def test_numeric_fragments_do_not_recover_saab(text):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence("SAAB", {"brand": text}) is None


@pytest.mark.parametrize("field", ["eeg_type_approval", "variant", "version", "model_no", "type_text"])
def test_saab_short_names_cannot_come_from_identifier_fields(field):
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence("SAAB", {field: "9-3"}) is None


def test_saab_field_provenance_does_not_prefer_ineligible_approval():
    assert ManufacturerCandidateIndex(saab_catalog()).recover_model_from_evidence(
        "SAAB", {"brand": "SAAB 9-3", "eeg_type_approval": "9/3"}
    ) == ("9 3", "brand")


def test_saab_scope_requires_catalog_and_family_prefix_not_trim_alias():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "Ford", "9-3", model_aliases=("9-3",)),
        VehicleCandidate("2", "SAAB", "900", model_aliases=("9-3",)),
    ))
    assert index.recover_model_from_evidence("Ford", {"brand": "9-3"}) is None
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3"}) is None
    assert ManufacturerCandidateIndex(()).recover_model_from_evidence("SAAB", {"brand": "9-5"}) is None


def test_saab_contradictory_names_and_unrecognized_explicit_model_fail_closed():
    index = ManufacturerCandidateIndex(saab_catalog())
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3 9-5"}) is None
    assert index.recover_model_from_evidence("SAAB", {"brand": "9-3", "model": "UNKNOWN"}) is None


def test_saab_recovery_preserves_ambiguity_and_technical_gates():
    evaluator = TecDocDryRunEvaluator(saab_catalog())
    payload = {"normalized": {"manufacturer": "SAAB"}, "source_evidence": {"brand": "SAAB 9-3"}}
    ambiguous = evaluator.evaluate(MatchSourceRecord(1, payload))
    assert ambiguous.terminal == "review_required"
    assert "route:candidate_margin_below_gate" in ambiguous.reason_codes
    assert "model_evidence_missing" not in ambiguous.reason_codes
    dated = evaluator.evaluate(MatchSourceRecord(2, {
        **payload, "normalized": {"manufacturer": "SAAB", "production_year": 2010},
    }))
    assert dated.terminal == "resolved"
    assert dated.top_candidate_reference == "4"
    conflicting = evaluator.evaluate(MatchSourceRecord(3, {
        **payload, "normalized": {"manufacturer": "SAAB", "production_year": 1980},
    }))
    assert conflicting.terminal != "resolved"
    assert "conflict:year" in conflicting.reason_codes


def porsche_catalog():
    return (
        VehicleCandidate("911", "PORSCHE", "911 (992)", model_aliases=("911",), year_from=2019,
                         power_kw=353, bodyworks=frozenset({"coupe"})),
        VehicleCandidate("GT", "PORSCHE", "CARRERA GT (980)", model_aliases=("CARRERA GT", "CARRERA"),
                         year_from=2003, year_to=2006),
    )


def test_the_word_in_the_model_position_outranks_a_longer_word_later():
    # "CARRERA" is longer, but the car is a 911: its model text says so first.
    assert ManufacturerCandidateIndex(porsche_catalog()).recover_model_from_evidence(
        "PORSCHE", {"model": "911 CARRERA 4 GTS"}
    ) == ("911 (992)", "model")


def test_a_911_registered_by_its_model_text_is_matched_as_a_911():
    result = TecDocDryRunEvaluator(porsche_catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "PORSCHE", "model_family": "911", "production_year": 2025},
        "source_evidence": {"brand": "PORSCHE", "model": "911 CARRERA 4 GTS"},
    }))
    assert result.top_candidate_reference == "911"
    assert "model_recovered_from_model" in result.reason_codes


@pytest.mark.parametrize(
    ("manufacturer", "catalog_model", "evidence", "expected"),
    [
        ("PEUGEOT", "307 (3A/C)", {"brand": "PEUGEOT 307 1,6"}, ("307 (3A/C)", "brand")),
        ("VOLVO", "940 (944)", {"brand": "VOLVO 9 + 940 1998"}, ("940 (944)", "brand")),
        ("ZEEKR", "7X", {"brand": "ZEEKR 7X"}, ("7X", "brand")),
        ("VW", "1500 (11)", {"brand": "VOLKSWAGEN 1500 LIM 113"}, ("1500 (11)", "brand")),
    ],
)
def test_a_number_or_short_name_is_a_model_in_the_model_position(manufacturer, catalog_model, evidence, expected):
    alias = catalog_model.split(" (")[0]
    index = ManufacturerCandidateIndex((VehicleCandidate("1", manufacturer, catalog_model, model_aliases=(alias,)),))
    assert index.recover_model_from_evidence(manufacturer, evidence) == expected


@pytest.mark.parametrize(
    "evidence",
    [
        {"variant": "307"},  # an identifier field has no model position
        {"brand": "307 PEUGEOT"},  # brand text that does not start with the make
        {"brand": "PEUGEOT PARTNER 307"},  # a number later in the text
        {"brand": "PEUGEOT 3"},  # one digit is never a model name alone
    ],
)
def test_a_number_elsewhere_is_not_a_model(evidence):
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "PEUGEOT", "307 (3A/C)", model_aliases=("307", "3")),
    ))
    assert index.recover_model_from_evidence("PEUGEOT", evidence) is None


def test_an_unspaced_registry_name_matches_the_spaced_catalog_name():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "TOYOTA", "RAV 4 IV (_A4_)", model_aliases=("RAV 4",)),
        VehicleCandidate("2", "TOYOTA", "RAV 4 V (_A5_, _H5_)", model_aliases=("RAV 4",)),
    ))
    assert index.recover_model_from_evidence("TOYOTA", {"brand": "TOYOTA RAV4"}) == ("RAV 4", "brand")
    # Only a letter-digit name gets an unspaced form: "C CLASS" never reads "CCLASS".
    assert index.recover_model_from_evidence("TOYOTA", {"brand": "TOYOTA RAV44"}) is None


def test_a_trim_shared_by_models_names_none_of_them():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("1", "VW", "Golf", model_aliases=("1 6 FSI",)),
        VehicleCandidate("2", "VW", "Polo", model_aliases=("1 6 FSI",)),
    ))
    # No model position in a variant field, and the longest label is a trim
    # shared by two models: nothing is recovered rather than a shorter guess.
    assert index.recover_model_from_evidence("VW", {"variant": "GOLF 1,6 FSI"}) is None
    # In brand text the model word decides.
    assert index.recover_model_from_evidence("VW", {"brand": "VW GOLF 1,6 FSI"}) == ("Golf", "brand")


def vw_catalog():
    return (
        VehicleCandidate("golf", "VW", "GOLF IV (1J1)", model_aliases=("GOLF",), year_from=1997, year_to=2005),
        VehicleCandidate("bora", "VW", "BORA I (1J2)", model_aliases=("BORA",), year_from=1998, year_to=2005),
    )


def _vw(model_family: str, brand: str, *, inferred: bool) -> MatchSourceRecord:
    return MatchSourceRecord(1, {
        "normalized": {"manufacturer": "VW", "model_family": model_family, "production_year": 2001},
        "source_evidence": {"brand": brand},
        **({"inferred_fields": ["model_family"]} if inferred else {}),
    })


def test_a_rule_inferred_model_yields_to_the_model_the_cars_text_names():
    result = TecDocDryRunEvaluator(vw_catalog()).evaluate(_vw("Golf", "VW BORA 1,6", inferred=True))

    assert result.top_candidate_reference == "bora"
    assert "model_recovered_from_brand" in result.reason_codes


def test_a_rule_inferred_model_is_used_when_the_text_names_no_model():
    result = TecDocDryRunEvaluator(vw_catalog()).evaluate(_vw("Golf", "VW", inferred=True))

    assert result.top_candidate_reference == "golf"
    assert "model_inferred_by_rule" in result.reason_codes


def test_a_model_the_registry_stated_is_not_second_guessed_by_brand_text():
    # Only an inferred model yields: a normalized model keeps today's behavior.
    result = TecDocDryRunEvaluator(vw_catalog()).evaluate(_vw("Golf", "VW BORA 1,6", inferred=False))

    assert result.top_candidate_reference == "golf"


def test_a_model_word_that_fits_several_models_never_yields_to_a_trim_word():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("b5", "VW", "PASSAT B5 (3B2)", model_aliases=("PASSAT",)),
        VehicleCandidate("cc", "VW", "CC B6 (357)", model_aliases=("PASSAT",)),
        VehicleCandidate("t3", "VW", "VARIANT II", model_aliases=("VARIANT",)),
    ))
    # PASSAT is the model word and the name of the B5 only (the CC merely carries it
    # as an alias); "VARIANT" later in the text never outranks it.
    assert index.recover_model_from_evidence("VW", {"brand": "VW PASSAT VARIANT CL 2,0"}) == (
        "PASSAT B5 (3B2)", "brand"
    )


def mercedes_catalog():
    return (
        VehicleCandidate("w203", "MERCEDES-BENZ", "C-CLASS (W203)", model_aliases=("C-CLASS",), year_from=2000),
        VehicleCandidate("s123", "MERCEDES-BENZ", "123 T-Model (S123)", model_aliases=("200 T",),
                         year_from=1977, year_to=1986),
    )


def test_anchored_only_reading_ignores_trim_words():
    index = ManufacturerCandidateIndex(mercedes_catalog())
    evidence = {"brand": "MERCEDES-BENZ C 200 T"}

    assert index.recover_model_from_evidence("MERCEDES-BENZ", evidence) == ("123 T-Model (S123)", "brand")
    assert index.recover_model_from_evidence("MERCEDES-BENZ", evidence, reading="strict") is None


def test_a_trim_word_does_not_overrule_a_rule_inferred_model():
    result = TecDocDryRunEvaluator(mercedes_catalog()).evaluate(MatchSourceRecord(1, {
        "normalized": {"manufacturer": "MERCEDES-BENZ", "model_family": "C-Class", "production_year": 2003},
        "source_evidence": {"brand": "MERCEDES-BENZ C 200 T"},
        "inferred_fields": ["model_family"],
    }))
    assert result.top_candidate_reference == "w203"
    assert "model_inferred_by_rule" in result.reason_codes


@pytest.mark.parametrize(
    ("label", "canonical", "expected"),
    [
        ("GOLF", "GOLF IV (1J1)", True),
        ("GOLF VARIANT", "GOLF VII Variant", True),
        ("FOCUS", "FOCUS I (DAW, DBW)", True),
        ("911", "911 (992)", True),
        ("RAV 4", "RAV 4 V (_A5_, _H5_)", True),
        ("PASSAT", "CC B6 (357)", False),
        ("200 T", "123 T-Model (S123)", False),
        ("ED", "CEE'D SW (ED)", False),
        ("VARIANT GOLF", "GOLF VII Variant", False),
    ],
)
def test_a_label_is_a_models_name_only_when_the_name_reads_so(label, canonical, expected):
    from ingestion.fuzzy_matching import _is_model_name

    assert _is_model_name(label, canonical) is expected


def test_a_chassis_code_in_the_model_position_does_not_hide_the_model_name():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("sw", "KIA", "CEE'D SW (ED)", model_aliases=("CEE D", "ED")),
        VehicleCandidate("hb", "KIA", "CEE'D Hatchback (ED)", model_aliases=("CEE D", "ED")),
    ))
    assert index.recover_model_from_evidence("KIA", {"model": "ED,CEE'D"}) == ("CEE D", "model")


def test_an_engine_number_in_the_model_position_does_not_hide_the_model_name():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("slk", "MERCEDES-BENZ", "SLK (R170)", model_aliases=("SLK", "200")),
        VehicleCandidate("w123", "MERCEDES-BENZ", "123 Saloon (W123)", model_aliases=("200",)),
    ))
    assert index.recover_model_from_evidence("MERCEDES-BENZ", {"brand": "MERCEDES-BENZ 200 SLK"}) == (
        "SLK (R170)", "brand"
    )


def test_a_model_name_behind_a_type_code_still_overrules_an_inferred_model():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("focus", "FORD", "FOCUS I (DAW, DBW)", model_aliases=("FOCUS",)),
        VehicleCandidate("kuga", "FORD", "KUGA I", model_aliases=("KUGA",)),
    ))
    assert index.recover_model_from_evidence("FORD", {"brand": "FORD DAW    FOCUS"}, reading="strict") == (
        "FOCUS I (DAW, DBW)", "brand"
    )


def test_the_model_word_names_its_family_even_without_an_alias_for_it():
    # TecDoc names every Passat with its generation; nothing spells PASSAT alone.
    index = ManufacturerCandidateIndex((
        VehicleCandidate("b5", "VW", "PASSAT B5 (3B2)"),
        VehicleCandidate("b6", "VW", "PASSAT B6 Variant (3C5)"),
        VehicleCandidate("t3", "VW", "VARIANT II", model_aliases=("VARIANT",)),
    ))
    evidence = {"brand": "VW PASSAT VARIANT CL 1,8"}

    assert index.recover_model_from_evidence("VW", evidence) == ("PASSAT", "brand")
    assert index.recover_model_from_evidence("VW", evidence, reading="strict") == ("PASSAT", "brand")


def test_a_body_word_elsewhere_never_overrules_an_inferred_model():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("w111", "MERCEDES-BENZ", "COUPE (W111, W112)", model_aliases=("COUPE",)),
        VehicleCandidate("w220", "MERCEDES-BENZ", "S-CLASS (W220)", model_aliases=("S-CLASS",)),
    ))
    assert index.recover_model_from_evidence(
        "MERCEDES-BENZ", {"brand": "MERCEDES S 600 COUPE"}, reading="strict"
    ) is None


def test_a_number_never_overrules_an_inferred_model():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("adenauer", "MERCEDES-BENZ", "300 (W186)"),
        VehicleCandidate("w124", "MERCEDES-BENZ", "E-CLASS (W124)", model_aliases=("E-CLASS",)),
    ))
    evidence = {"brand": "MERCEDES-BENZ 300 TD"}

    # A number is no family word: "300" is an engine as often as the 1950s 300.
    assert index.recover_model_from_evidence("MERCEDES-BENZ", evidence) is None
    assert index.recover_model_from_evidence("MERCEDES-BENZ", evidence, reading="strict") is None


def test_amg_is_part_of_the_mercedes_make_and_grand_names_no_family_alone():
    from ingestion.fuzzy_matching import model_position_token

    assert model_position_token("brand", "MERCEDES-AMG C 63 AMG", "MERCEDES BENZ") == "C"
    index = ManufacturerCandidateIndex((
        VehicleCandidate("gc4", "CITROEN", "GRAND C4 PICASSO I (UA_)"),
        VehicleCandidate("gcm", "CITROEN", "GRAND C-MAX (DXA/CB7, DXA/CEU)"),
    ))
    assert index.recover_model_from_evidence("CITROEN", {"brand": "CITROEN GRAND"}, reading="strict") is None


@pytest.mark.parametrize(
    ("text", "catalog"),
    [
        ("PORSCHE 911 CARRERA", ("911 (992)", "CARRERA GT (980)")),
        ("AUDI 100 QUATTRO", ("100 (44, 44Q, C3)", "QUATTRO (85)")),
    ],
)
def test_a_number_in_the_model_position_still_stops_later_words_being_read(text, catalog):
    manufacturer = text.split()[0]
    index = ManufacturerCandidateIndex(tuple(
        VehicleCandidate(str(n), manufacturer, model, model_aliases=(model.split(" (")[0],))
        for n, model in enumerate(catalog)
    ))
    assert index.recover_model_from_evidence(manufacturer, {"brand": text}, reading="strict") is None


@pytest.mark.parametrize(
    ("value", "make", "word"),
    [("TOYOTA BZ4X", "TOYOTA", "BZ4X"), ("MB SL 500", "MERCEDES BENZ", "SL"), ("911 CARRERA", "PORSCHE", "911"),
     ("VOLVO", "VOLVO", "VOLVO")],
)
def test_the_model_fields_model_word_skips_a_repeated_make(value, make, word):
    from ingestion.fuzzy_matching import model_position_token

    assert model_position_token("model", value, make) == word


def test_the_text_may_name_the_inferred_family_more_precisely_but_never_another():
    catalog = (
        VehicleCandidate("e24", "BMW", "6 (E24)", model_aliases=("630 CS",), year_from=1976, year_to=1989),
        VehicleCandidate("e63", "BMW", "6 (E63)", year_from=2004, year_to=2010),
        VehicleCandidate("e30", "BMW", "3 (E30)", model_aliases=("325 I",), year_from=1982, year_to=1994),
    )

    def evaluate(brand: str, inferred: str):
        return TecDocDryRunEvaluator(catalog).evaluate(MatchSourceRecord(1, {
            "normalized": {"manufacturer": "BMW", "model_family": inferred, "production_year": 1978},
            "source_evidence": {"brand": brand},
            "inferred_fields": ["model_family"],
        }))

    precise = evaluate("BMW 630 CS", "6 Series")
    assert precise.top_candidate_reference == "e24"
    assert "model_recovered_from_brand" in precise.reason_codes
    # "325 I" names a 3 Series: another family, so the rule's 6 Series stands.
    assert "model_inferred_by_rule" in evaluate("BMW 325 I", "6 Series").reason_codes


def test_a_label_continuing_the_model_name_reads_it_more_precisely():
    index = ManufacturerCandidateIndex((
        VehicleCandidate("w201", "MERCEDES-BENZ", "190 (W201)", model_aliases=("190",)),
        VehicleCandidate("w121", "MERCEDES-BENZ", "PONTON (W121)", model_aliases=("190 B", "190 SL")),
    ))
    assert index.recover_model_from_evidence("MERCEDES-BENZ", {"brand": "MERCEDES-BENZ 190 B"}) == (
        "PONTON (W121)", "brand"
    )
    assert index.recover_model_from_evidence("MERCEDES-BENZ", {"brand": "MERCEDES-BENZ 190 E"}) == (
        "190 (W201)", "brand"
    )


def test_a_make_that_is_also_the_model_name_is_still_read():
    index = ManufacturerCandidateIndex((VehicleCandidate("g", "GALLOPER", "GALLOPER", year_from=1997),))
    assert index.recover_model_from_evidence("GALLOPER", {"brand": "GALLOPER 2,5 TCI A/C"}) == (
        "GALLOPER", "brand"
    )


def test_an_alias_borrowed_by_a_broader_family_does_not_hide_the_models_it_names():
    # A reviewed rule maps "RANGE ROVER EVOQUE" to the TS family "Range Rover", so
    # the alias lands on every Range Rover; it names only the Evoques.
    everywhere = ("RANGE ROVER", "RANGE ROVER EVOQUE")
    index = ManufacturerCandidateIndex((
        VehicleCandidate("l405", "LAND ROVER", "RANGE ROVER IV (L405)", model_aliases=everywhere),
        VehicleCandidate("l538", "LAND ROVER", "RANGE ROVER EVOQUE (L538)", model_aliases=everywhere),
        VehicleCandidate("l551", "LAND ROVER", "RANGE ROVER EVOQUE (L551)", model_aliases=everywhere),
    ))
    assert index.recover_model_from_evidence("LAND ROVER", {"model": "RANGE ROVER EVOQUE"}) == (
        "RANGE ROVER EVOQUE", "model"
    )
