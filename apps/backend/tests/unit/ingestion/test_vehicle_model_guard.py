"""The model guard: a learned model never contradicts the model word in the car's own text."""

import pytest

from ingestion.fuzzy_matching import VehicleCandidate, same_model_family
from ingestion.tecdoc.match_run_adapters import TecDocDryRunEvaluator
from ingestion.vehicle_model_guard import ModelGuard, count_verdicts

CATALOG = (
    VehicleCandidate("golf", "VW", "GOLF IV (1J1)", model_aliases=("GOLF",), year_from=1997, year_to=2005),
    VehicleCandidate("bora", "VW", "BORA I (1J2)", model_aliases=("BORA",), year_from=1998, year_to=2005),
    VehicleCandidate("e30", "BMW", "3 (E30)", model_aliases=("3",), year_from=1982, year_to=1994),
    VehicleCandidate("w116", "MERCEDES-BENZ", "S-CLASS (W116)", model_aliases=("S-CLASS",),
                     year_from=1972, year_to=1980),
    VehicleCandidate("s123", "MERCEDES-BENZ", "123 T-Model (S123)", model_aliases=("200 T",),
                     year_from=1977, year_to=1986),
    VehicleCandidate("ceed", "KIA", "CEE'D (ED)", year_from=2006, year_to=2012),
    VehicleCandidate("bk", "MAZDA", "3 (BK)", year_from=2003, year_to=2009),
)


@pytest.fixture(scope="module")
def guard() -> ModelGuard:
    return ModelGuard(TecDocDryRunEvaluator(CATALOG))


@pytest.mark.parametrize(
    ("value", "catalog_model", "manufacturer", "expected"),
    [
        ("Golf", "GOLF IV (1J1)", "VW", True),
        ("Golf", "BORA I (1J2)", "VW", False),
        ("3 Series", "3 (E46)", "BMW", True),
        ("5 Series", "3 (E46)", "BMW", False),
        ("C-Class", "C-CLASS Coupe (CL203)", "MERCEDES-BENZ", True),
        ("Ceed", "CEE'D (ED)", "KIA", True),
        ("Mazda3", "3 (BK)", "MAZDA", True),
        ("XC40", "EX40 (536)", "VOLVO", False),
        ("V70", "XC70 I Cross Country (295)", "VOLVO", False),
        ("307", "307 SW (3H)", "PEUGEOT", True),
        ("3 Series", "320", "BMW", True),
        ("3 Series", "520", "BMW", False),
        ("SLK", "170", "MERCEDES-BENZ", False),
        # TecDoc puts the make in front of a model named by a number.
        ("4", "POLESTAR 4 (004)", "POLESTAR", True),
        ("2", "POLESTAR 4 (004)", "POLESTAR", False),
        ("H2", "HUMMER H2", "HUMMER", True),
        # A model number or letter after a shared word is the model itself.
        ("ID.4", "ID.3 (E11, E12)", "VW", False),
        ("ID.4", "ID.4 (E21)", "VW", True),
        ("Ioniq 5", "IONIQ 6 (CE)", "HYUNDAI", False),
        ("Model 3", "MODEL Y", "TESLA", False),
        ("Atto 2", "ATTO 3", "BYD", False),
        ("Ds 3", "DS 4 (NX_)", "DS", False),
        # A generation numeral or a word is not a model number.
        ("Golf 7", "GOLF VII (5G1)", "VW", True),
        ("Galaxie 500", "GALAXIE", "FORD", True),
    ],
)
def test_same_family(value: str, catalog_model: str, manufacturer: str, expected: bool) -> None:
    assert same_model_family(value, catalog_model, manufacturer) is expected


def test_text_that_names_another_model_refuses_the_fill(guard: ModelGuard) -> None:
    verdict = guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": "VW BORA 1,6"})

    assert verdict is not None
    assert (verdict.reason, verdict.detail) == ("text_names_another_model", "BORA I (1J2)")


@pytest.mark.parametrize(
    ("manufacturer", "model", "brand"),
    [
        ("VW", "Golf", "VW GOLF 1,6"),  # the text agrees
        ("VW", "Golf", "VW"),  # the text names no model
        ("BMW", "3 Series", "BMW 318I"),  # same family, spelled differently
        ("KIA", "Ceed", "KIA CEED 1,6"),
        ("MAZDA", "Mazda3", "MAZDA MAZDA3"),
        # A trim word is weaker evidence than the rule: "200 T" is on the 123
        # T-Model, but "C" is the model word and names no catalog model here.
        ("MERCEDES-BENZ", "C-Class", "MERCEDES-BENZ C 200 T"),
        # A model a catalog lacks for the car's era is not contradicted by it.
        ("MERCEDES-BENZ", "S-Class", "MERCEDES-BENZ"),
    ],
)
def test_a_fill_the_model_word_does_not_contradict_is_kept(
    guard: ModelGuard, manufacturer: str, model: str, brand: str
) -> None:
    assert guard.verdict(manufacturer=manufacturer, model_family=model, evidence={"brand": brand}) is None


def test_the_registry_model_field_counts_as_the_model_word(guard: ModelGuard) -> None:
    verdict = guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": "VW", "model": "BORA"})

    assert verdict is not None and verdict.detail == "BORA I (1J2)"


def test_verdicts_are_counted_by_reason(guard: ModelGuard) -> None:
    verdicts = [
        guard.verdict(manufacturer="VW", model_family="Golf", evidence={"brand": b})
        for b in ("VW BORA", "VW BORA 1,6", "VW GOLF")
    ]
    assert count_verdicts(verdicts) == {"text_names_another_model": 2}


def test_gr_spelled_out_as_grand_can_name_the_filled_family() -> None:
    catalog = (
        VehicleCandidate("voy", "CHRYSLER", "VOYAGER IV (RG, RS)", model_aliases=("VOYAGER",), year_from=2000),
        VehicleCandidate("gvoy", "CHRYSLER", "GRAND VOYAGER IV (RG)", model_aliases=("GRAND VOYAGER",),
                         year_from=2000),
    )
    guard = ModelGuard(TecDocDryRunEvaluator(catalog))

    assert guard.verdict(manufacturer="CHRYSLER", model_family="Grand Voyager",
                         evidence={"brand": "CHRYSLER GR VOYAGER 3.3"}) is None
    # Spelled out or not, the text names no Sebring.
    assert guard.verdict(manufacturer="CHRYSLER", model_family="Sebring",
                         evidence={"brand": "CHRYSLER GR VOYAGER 3.3"}) is not None


def test_the_fuel_refuses_a_combustion_family_for_an_electric_car(guard: ModelGuard) -> None:
    verdict = guard.verdict(manufacturer="Ford", model_family="Mustang",
                            evidence={"brand": "FORD MUSTANG"}, fuel="electricity")

    assert verdict is not None and (verdict.reason, verdict.detail) == ("fuel_names_another_model", "Mustang Mach-e")
    assert guard.verdict(manufacturer="Ford", model_family="Mustang",
                         evidence={"brand": "FORD MUSTANG"}, fuel="petrol") is None


def test_a_family_sold_under_another_name_is_the_same_family() -> None:
    from ingestion.tecdoc.match_run_adapters import reviewed_export_names

    # The catalog loaders add the export name to TecDoc's YUAN PLUS.
    yuan_plus = VehicleCandidate("yp", "BYD", "YUAN PLUS", model_aliases=reviewed_export_names("BYD", "YUAN PLUS"),
                                 year_from=2022)
    atto_2 = VehicleCandidate("a2", "BYD", "ATTO 2", year_from=2024)
    guard = ModelGuard(TecDocDryRunEvaluator((yuan_plus, atto_2)))

    assert guard.verdict(manufacturer="BYD", model_family="Atto 3", evidence={"model": "ATTO 3"}) is None
    # "ATTO 3" no longer reads as TecDoc's ATTO 2 for sharing the word "ATTO".
    assert TecDocDryRunEvaluator((yuan_plus, atto_2)).source_text_model("BYD", {"model": "ATTO 3"}) != ("BYD", "ATTO 2")
    assert TecDocDryRunEvaluator((yuan_plus, atto_2)).source_text_model("BYD", {"model": "ATTO 2"}) == ("BYD", "ATTO 2")


def test_export_names_are_scoped_to_their_manufacturer_and_family() -> None:
    from ingestion.tecdoc.match_run_adapters import reviewed_export_names

    assert reviewed_export_names("BYD", "YUAN PLUS") == ("ATTO 3",)
    assert reviewed_export_names("byd", "YUAN PLUS (SC2E)") == ("ATTO 3",)
    assert reviewed_export_names("BYD", "ATTO 2") == ()
    assert reviewed_export_names("TOYOTA", "YUAN PLUS") == ()


TEXT_CATALOG = (
    VehicleCandidate("sl", "KIA", "SPORTAGE III (SL)", year_from=2010, year_to=2016),
    VehicleCandidate("xm", "KIA", "SORENTO II (XM)", year_from=2009, year_to=2015),
    VehicleCandidate("dj", "MAZDA", "2 Hatchback (DL, DJ)", year_from=2014),
    VehicleCandidate("dk", "MAZDA", "CX-3 (DK)", year_from=2015),
    VehicleCandidate("b7", "VW", "PASSAT B7 (362)", year_from=2010, year_to=2015),
    VehicleCandidate("cc", "VW", "CC B7 (358)", year_from=2011, year_to=2016),
    VehicleCandidate("ex30", "VOLVO", "EX30 (416)", year_from=2023),
    VehicleCandidate("ex30cc", "VOLVO", "EX30 Cross Country", year_from=2024),
    VehicleCandidate("440", "VOLVO", "440 (445)", year_from=1987, year_to=1997),
    VehicleCandidate("doblo", "FIAT", "DOBLO MPV (119_, 223_)", year_from=2001, year_to=2010),
    VehicleCandidate("500", "FIAT", "500 (312_)", year_from=2007),
)
#: The make's model families as TS spells them, with the reviewed names.
VOCABULARY = {
    "Kia": {"Sportage", "Sorento", "Ceed"},
    "Mazda": {"2", "3", "6", "CX-3", "CX-5", "Mazda3"},
    "Volkswagen": {"Passat", "Golf", "CC"},
    "Volvo": {"EX30", "EX30 Cross Country", "440", "Duett", "940", "960"},
    "Fiat": {"500", "Doblo", "Coupe"},
    "Mercedes-Benz": {"S-Class", "Sprinter", "B-Class"},
}


@pytest.fixture(scope="module")
def text_guard() -> ModelGuard:
    return ModelGuard(TecDocDryRunEvaluator(TEXT_CATALOG), VOCABULARY, {"Volkswagen": {"VW", "VOLKSWAGEN"}})


def test_an_unknown_model_field_lets_the_brand_text_name_the_model(text_guard: ModelGuard) -> None:
    # "SL" is the Sportage's generation code; the catalog has no model of that name.
    verdict = text_guard.verdict(manufacturer="KIA", model_family="Sorento",
                                 evidence={"model": "SL", "brand": "KIA SPORTAGE 2,0 CRDI EX"})

    assert verdict is not None and verdict.reason == "text_names_another_model"
    assert verdict.detail.startswith("SPORTAGE")
    assert text_guard.verdict(manufacturer="KIA", model_family="Sportage",
                              evidence={"model": "SL", "brand": "KIA SPORTAGE 2,0 CRDI EX"}) is None


def test_a_model_field_the_catalog_reads_keeps_the_brand_text_out() -> None:
    guard = ModelGuard(TecDocDryRunEvaluator(TEXT_CATALOG))
    # The two texts name two models: the guard does not take sides.
    assert guard.verdict(manufacturer="KIA", model_family="Sorento",
                         evidence={"model": "SORENTO", "brand": "KIA SPORTAGE 2,0"}) is None


@pytest.mark.parametrize(
    ("manufacturer", "model", "evidence", "detail"),
    [
        # The make glued to the number: the 2, not the CX-3 its type code taught.
        ("Mazda", "CX-3", {"model": "MAZDA2", "brand": "MAZDA"}, "2"),
        ("Mazda", "CX-5", {"model": "MAZDA6", "brand": "MAZDA"}, "6"),
        # A reviewed generation code, with nothing else to read.
        ("Kia", "Sorento", {"model": "SL", "brand": "KIA"}, "Sportage"),
        # A reviewed name the catalog spells otherwise ("CC B7").
        ("Volkswagen", "Passat", {"model": "CC", "brand": "VOLKSWAGEN"}, "CC"),
        # Reviewed abbreviations.
        ("Fiat", "500", {"brand": "FIAT DOBL 1,6"}, "Doblo"),
        ("Fiat", "500", {"brand": "FIAT COUP 20V TURBO"}, "Coupe"),
        # "P 445" is the 1950s Duett, not the 440.
        ("Volvo", "440", {"brand": "VOLVO P 44507 M"}, "Duett"),
    ],
)
def test_a_text_that_names_another_family_in_ts_words_refuses_the_fill(
    text_guard: ModelGuard, manufacturer: str, model: str, evidence: dict[str, str], detail: str
) -> None:
    verdict = text_guard.verdict(manufacturer=manufacturer, model_family=model, evidence=evidence)

    assert verdict is not None and (verdict.reason, verdict.detail) == ("text_names_another_family", detail)


@pytest.mark.parametrize(
    ("manufacturer", "model", "evidence"),
    [
        ("Mazda", "2", {"model": "MAZDA2", "brand": "MAZDA"}),
        ("Mazda", "Mazda3", {"model": "MAZDA3", "brand": "MAZDA"}),
        ("Mazda", "3", {"model": "MAZDA3", "brand": "MAZDA"}),
        ("Kia", "Sportage", {"model": "SL", "brand": "KIA"}),
        ("Volkswagen", "Passat", {"model": "PASSAT CC", "brand": "VOLKSWAGEN"}),
        ("Volvo", "440", {"brand": "VOLVO 445-183 GL"}),
        # The texts name nothing: the rule stands.
        ("Mazda", "CX-3", {"brand": "MAZDA"}),
        # One text names the fill: texts that disagree refuse nothing.
        ("Mazda", "CX-3", {"model": "MAZDA2", "brand": "MAZDA CX-3"}),
    ],
)
def test_a_text_that_names_the_family_in_ts_words_keeps_the_fill(
    text_guard: ModelGuard, manufacturer: str, model: str, evidence: dict[str, str]
) -> None:
    assert text_guard.verdict(manufacturer=manufacturer, model_family=model, evidence=evidence) is None


def test_ts_words_are_read_only_with_a_vocabulary() -> None:
    guard = ModelGuard(TecDocDryRunEvaluator(TEXT_CATALOG))

    assert guard.verdict(manufacturer="Mazda", model_family="CX-3", evidence={"model": "MAZDA2"}) is None


def test_a_model_field_that_is_exactly_a_name_refuses_a_narrower_sibling(text_guard: ModelGuard) -> None:
    verdict = text_guard.verdict(manufacturer="Volvo", model_family="EX30 Cross Country",
                                 evidence={"model": "EX30", "brand": "VOLVO"})

    assert verdict is not None and (verdict.reason, verdict.detail) == ("model_text_names_another_family", "EX30")
    for evidence in (
        {"model": "EX30 CROSS COUNTRY", "brand": "VOLVO"},
        # Another text names the narrower family.
        {"model": "EX30", "brand": "VOLVO EX30 CROSS COUNTRY"},
        # A model field saying more than the name is no exact name.
        {"model": "EX30 TWIN MOTOR", "brand": "VOLVO"},
    ):
        assert text_guard.verdict(manufacturer="Volvo", model_family="EX30 Cross Country", evidence=evidence) is None
    assert text_guard.verdict(manufacturer="Volvo", model_family="EX30", evidence={"model": "EX30"}) is None


def test_a_motorhome_converter_keeps_only_the_vans_it_builds_on(text_guard: ModelGuard) -> None:
    verdict = text_guard.verdict(manufacturer="Mercedes-Benz", model_family="S-Class",
                                 evidence={"brand": "MERCEDES-BENZ RAPIDO", "model": "S80"})

    assert verdict is not None and (verdict.reason, verdict.detail) == ("text_names_a_converter", "RAPIDO")
    assert text_guard.verdict(manufacturer="Mercedes-Benz", model_family="Sprinter",
                              evidence={"brand": "MERCEDES-BENZ RAPIDO"}) is None


def test_a_two_series_volvo_text_takes_the_engines_series(text_guard: ModelGuard) -> None:
    evidence = {"brand": "VOLVO 944-964"}

    verdict = text_guard.verdict(manufacturer="Volvo", model_family="940", evidence=evidence, engine_code="B6304F")
    assert verdict is not None and (verdict.reason, verdict.detail) == ("engine_names_another_model", "960")
    # Same text, another engine: the engine is not part of what the guard caches.
    assert text_guard.verdict(manufacturer="Volvo", model_family="940", evidence=evidence, engine_code="B230FB") is None
    assert text_guard.verdict(manufacturer="Volvo", model_family="940", evidence=evidence) is None
    # A 960 under the same range: the engine names the fill, and the text names neither.
    assert text_guard.verdict(manufacturer="Volvo", model_family="960", evidence=evidence, engine_code="B6304F") is None
    assert text_guard.verdict(manufacturer="Volvo", model_family="960", evidence=evidence) is None


def test_a_two_series_text_names_neither_series(text_guard: ModelGuard) -> None:
    for brand, family in (("VOLVO 744-764", "760"), ("VOLVO 745-765", "740"), ("VOLVO 945 965", "960")):
        assert text_guard.verdict(manufacturer="Volvo", model_family=family, evidence={"brand": brand}) is None


RENAULT = {"Renault": {"Clio", "Megane", "B", "4"}}
#: A catalog of another make: these readings are TS's words alone.
NO_CATALOG = (VehicleCandidate("t603", "TATRA", "T 603", year_from=1956, year_to=1975),)


def test_a_reading_the_ts_named_cars_with_that_text_contradict_is_none() -> None:
    # "B" stands for the vocabulary's rare misread family: TS states Clio for all 60
    # cars with the brand text "RENAULT B", the Clio II's body code.
    stated = {("Renault", "brand", "RENAULT B"): {"Clio": 60}}
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), RENAULT, stated_texts=stated)

    assert guard.verdict(manufacturer="Renault", model_family="Clio", evidence={"brand": "RENAULT B"}) is None
    # Without the TS-named cars the reading stands, as it does for another text.
    unchecked = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), RENAULT)
    verdict = unchecked.verdict(manufacturer="Renault", model_family="Clio", evidence={"brand": "RENAULT B"})
    assert verdict is not None and (verdict.reason, verdict.detail) == ("text_names_another_family", "B")


def test_one_stray_ts_named_car_does_not_overrule_a_reading() -> None:
    stated = {("Mazda", "model", "MAZDA2"): {"CX-3": 1}}
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), VOCABULARY, stated_texts=stated)

    verdict = guard.verdict(manufacturer="Mazda", model_family="CX-3", evidence={"model": "MAZDA2"})
    assert verdict is not None and verdict.detail == "2"


def test_a_reviewed_non_family_is_no_fill(text_guard: ModelGuard) -> None:
    verdict = text_guard.verdict(manufacturer="Renault", model_family="B", evidence={"brand": "RENAULT B -"})

    assert verdict is not None and (verdict.reason, verdict.detail) == ("not_a_family", "B")


def test_a_car_ts_has_no_family_for_fits_no_fill() -> None:
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), RENAULT)

    verdict = guard.verdict(manufacturer="Renault", model_family="4", evidence={"brand": "RENAULT 4 CV R 1062"})
    assert verdict is not None and (verdict.reason, verdict.detail) == ("text_names_another_car", "4CV")
    assert guard.verdict(manufacturer="Renault", model_family="4", evidence={"brand": "RENAULT 4 L"}) is None


@pytest.mark.parametrize(
    ("manufacturer", "model", "evidence", "make_words"),
    [
        # The E34 525iX, not the electric iX: "BMW525", the make glued to a number,
        # is no make word.
        ("BMW", "5 Series", {"brand": "BMW 525 IX TOURING"}, {"BMW", "BMW525"}),
        # A TEC motorhome on a Transit: "FORD-T" is no make in "FORD T.E.C".
        ("Ford", "Transit", {"brand": "FORD T.E.C CARAVAN GMBH", "model": "FREETEC TI 708"}, {"FORD", "FORD-T"}),
        # An engine size names no Mazda 2.
        ("Mazda", "6", {"brand": "MAZDA 2.3 KOMBI SPORT E"}, {"MAZDA"}),
        # "MERCEDES B" abbreviates Mercedes-Benz.
        ("Mercedes-Benz", "E-Class", {"brand": "MERCEDES B E240 AVANTGAR"}, {"MERCEDES"}),
        # "CHEVY" is the make in front of the Caprice.
        ("Chevrolet", "Caprice", {"brand": "CHEVY CAPRICE CLASSIC"}, {"CHEVY", "CHEVROLET"}),
    ],
)
def test_texts_the_reader_once_misread_keep_the_fill(
    manufacturer: str, model: str, evidence: dict[str, str], make_words: set[str]
) -> None:
    vocabulary = {
        "BMW": {"3 Series", "5 Series", "iX"},
        "Ford": {"E", "Transit"},
        "Audi": {"A4", "A5", "Coupe", "Cabriolet"},
        "Mazda": {"2", "6"},
        "Mercedes-Benz": {"B-Class", "E-Class"},
        "Chevrolet": {"Chevy", "Caprice"},
    }
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), vocabulary, {manufacturer: make_words})

    assert guard.verdict(manufacturer=manufacturer, model_family=model, evidence=evidence) is None


def test_an_s_model_coupe_or_cabriolet_is_not_audis_old_coupe_or_cabriolet() -> None:
    vocabulary = {"Audi": {"A4", "A5", "TT", "Coupe", "Cabriolet"}}
    stated = {("Audi", "brand", "AUDI S5 COUPE 4,2Q"): {"A5": 14}}
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), vocabulary, stated_texts=stated)

    # TS states A5 for the 14 cars it named with this very text.
    assert guard.verdict(manufacturer="Audi", model_family="A5", evidence={"brand": "AUDI S5 COUPE 4,2Q"}) is None
    # The old Coupe and Cabriolet were built until 1996 and 2000.
    for brand, family, year in (
        ("AUDI S5 COUPE", "A5", 2008), ("AUDI RS4 CABRIOLET", "A4", 2006), ("AUDI TTS COUPÉ", "TT", 2021)
    ):
        assert guard.verdict(manufacturer="Audi", model_family=family, evidence={"brand": brand}, year=year) is None
    # The S2 Coupé of 1993 is the Coupe.
    verdict = guard.verdict(manufacturer="Audi", model_family="A4", evidence={"brand": "AUDI S2 COUPE"}, year=1993)
    assert verdict is not None and verdict.detail == "Coupe"


def test_ts_spellings_of_one_family_match_in_both_directions() -> None:
    from ingestion.vehicle_model_guard import _same_ts_family

    assert _same_ts_family("Mazda3", "3", "Mazda")
    assert _same_ts_family("3", "Mazda3", "Mazda")
    assert not _same_ts_family("3", "Mazda2", "Mazda")
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), {"Mazda": {"2", "3", "Mazda3"}})
    # "MAZDA 3" reads as Mazda3, "3" as 3: either fill is the text's family.
    assert guard.verdict(manufacturer="Mazda", model_family="3", evidence={"model": "MAZDA 3"}) is None
    assert guard.verdict(manufacturer="Mazda", model_family="Mazda3", evidence={"model": "3"}) is None
    assert guard.verdict(manufacturer="Mazda", model_family="2", evidence={"model": "MAZDA 3"}) is not None


def test_names_ts_files_together_and_names_on_hold_refuse_nothing() -> None:
    vocabulary = {
        "BMW": {"X1", "iX1", "X3"},
        "Volkswagen": {"Multivan", "Caravelle", "Golf"},
        "Renault": {"Megane", "Scenic"},
    }
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), vocabulary, {"Volkswagen": {"VW"}})

    # TS files every iX1 it names under the X1.
    assert guard.verdict(manufacturer="BMW", model_family="X1", evidence={"brand": "BMW IX1 EDRIVE20"}) is None
    assert guard.verdict(manufacturer="BMW", model_family="iX1", evidence={"model": "X1"}) is None
    assert guard.verdict(manufacturer="BMW", model_family="X3", evidence={"brand": "BMW IX1 EDRIVE20"}) is not None
    # Multivan or Caravelle, Scenic or Megane: on hold with the data owner.
    assert guard.verdict(manufacturer="Volkswagen", model_family="Multivan", evidence={"model": "CARAVELLE"}) is None
    assert guard.verdict(manufacturer="Volkswagen", model_family="Multivan",
                         evidence={"brand": "VW CARAVELLE 2,5"}) is None
    assert guard.verdict(manufacturer="Renault", model_family="Megane",
                         evidence={"brand": "RENAULT GD SCENIC DCI130"}) is None
    assert guard.verdict(manufacturer="Volkswagen", model_family="Golf", evidence={"model": "CARAVELLE"}) is not None


def test_a_model_field_that_names_the_fill_outranks_the_brand_text() -> None:
    catalog = (
        VehicleCandidate("xk8", "JAGUAR", "XK8 (QEV)", year_from=1996, year_to=2006),
        VehicleCandidate("bel", "PLYMOUTH", "BELVEDERE", year_from=1954, year_to=1970),
    )
    vocabulary = {"Jaguar": {"XK8", "XKR"}, "Plymouth": {"Belvedere", "GTX", "Road Runner"}}
    guard = ModelGuard(TecDocDryRunEvaluator(catalog), vocabulary)
    xkr = {"brand": "JAGUAR XK8", "model": "XKR CONVERTIBLE"}
    gtx = {"brand": "PLYMOUTH BELVEDERE", "model": "GTX/SPORT"}

    assert guard.verdict(manufacturer="Jaguar", model_family="XKR", evidence=xkr) is None
    assert guard.verdict(manufacturer="Plymouth", model_family="GTX", evidence=gtx) is None
    # Without TS's words the catalog's reading of the brand text refuses them.
    catalog_only = ModelGuard(TecDocDryRunEvaluator(catalog))
    assert catalog_only.verdict(manufacturer="Jaguar", model_family="XKR", evidence=xkr) is not None
    assert catalog_only.verdict(manufacturer="Plymouth", model_family="GTX", evidence=gtx) is not None


LEXUS_RULE = "MOD-VIN-942ba1671c82aef9"


@pytest.fixture(scope="module")
def keyed_guard() -> ModelGuard:
    vocabulary = {"Lexus": {"IS", "LS"}, "Volkswagen": {"Passat", "CC"}, "Volvo": {"EX30", "EX30 Cross Country"}}
    # TS itself states IS, LS and Passat; CC and EX30 are reviewed names it never uses.
    stated = {"Lexus": {"IS", "LS"}, "Volkswagen": {"Passat"}, "Volvo": {"EX30 Cross Country"}}
    eras = {LEXUS_RULE: (2005, 2013), "MOD-VIN-passat": (1997, 2015), "MOD-VIN-ex30cc": (2025, 2025)}
    return ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), vocabulary, stated_families=stated, vin_rule_eras=eras)


def test_a_unanimous_vin_rule_outranks_one_cars_text_in_its_years(keyed_guard: ModelGuard) -> None:
    misspelt = {"brand": "LEXUS LS250"}

    assert keyed_guard.verdict(manufacturer="Lexus", model_family="IS", evidence=misspelt, year=2008,
                               rule_id=LEXUS_RULE) is None
    # Another rule, a car outside the key's years, or no year: the text decides.
    for year, rule_id in ((2008, "MOD-TP-0000000000000000"), (1990, LEXUS_RULE), (None, LEXUS_RULE)):
        verdict = keyed_guard.verdict(manufacturer="Lexus", model_family="IS", evidence=misspelt, year=year,
                                      rule_id=rule_id)
        assert verdict is not None and verdict.detail == "LS"
    # Inclusive of the learned years and their slack.
    assert keyed_guard.verdict(manufacturer="Lexus", model_family="IS", evidence=misspelt, year=2015,
                               rule_id=LEXUS_RULE) is None
    # TS never uses the name the text reads: its absence under the key says nothing.
    cc = keyed_guard.verdict(manufacturer="Volkswagen", model_family="Passat", evidence={"model": "CC"},
                             year=2013, rule_id="MOD-VIN-passat")
    assert cc is not None and (cc.reason, cc.detail) == ("text_names_another_family", "CC")


def test_a_vin_rule_lets_a_narrower_sibling_stand(keyed_guard: ModelGuard) -> None:
    ex30 = {"model": "EX30", "brand": "VOLVO"}

    assert keyed_guard.verdict(manufacturer="Volvo", model_family="EX30 Cross Country", evidence=ex30, year=2025,
                               rule_id="MOD-VIN-ex30cc") is None
    # The type code every EX30 carries does not tell the Cross Country apart.
    verdict = keyed_guard.verdict(manufacturer="Volvo", model_family="EX30 Cross Country", evidence=ex30,
                                  year=2025, rule_id="MOD-TP-c6ed94e7b6eb0189")
    assert verdict is not None and verdict.reason == "model_text_names_another_family"


def test_a_reading_outside_its_familys_years_names_no_family() -> None:
    vocabulary = {"Audi": {"A4", "Cabriolet", "Coupe"}, "Ford": {"Galaxy", "Galaxie"}}
    guard = ModelGuard(TecDocDryRunEvaluator(NO_CATALOG), vocabulary)
    cabrio = {"brand": "AUDI CABRIO 2,4"}

    # The A4 Cabriolet of 2002 is not Audi's 1991-2000 Cabriolet.
    assert guard.verdict(manufacturer="Audi", model_family="A4", evidence=cabrio, year=2002) is None
    assert guard.verdict(manufacturer="Audi", model_family="A4", evidence=cabrio, year=1995) is not None
    assert guard.verdict(manufacturer="Audi", model_family="A4", evidence=cabrio) is not None
    # A 2009 "FORD GALAXIE" misspells the Galaxy.
    assert guard.verdict(manufacturer="Ford", model_family="Galaxy", evidence={"brand": "FORD GALAXIE"},
                         year=2009) is None
