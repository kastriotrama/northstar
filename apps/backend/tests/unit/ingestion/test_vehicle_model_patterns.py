"""Reviewed patterns: which model a registry text's model word names, in TS's own spelling."""

import pytest

from ingestion.vehicle_model_patterns import (
    REVIEWED_MODEL_NAMES,
    model_text_family,
    pattern_model,
    preferred_spellings,
)

VOLVO = {"740", "240", "850", "940", "760", "140", "V70", "Amazon", "164", "P 1800", "Duett", "340", "460"}
BMW = {"1 Series", "3 Series", "5 Series", "X3", "Z3"}
MERCEDES = {"C-Class", "E-Class", "S-Class", "M-Class", "SLK"}
SAAB = {"900", "9000", "9-3", "96", "99", "95"}


@pytest.mark.parametrize(
    ("manufacturer", "token", "vocabulary", "expected"),
    [
        # A word TS already names this make's cars by, in TS's spelling.
        ("Toyota", "COROLLA", {"Corolla", "RAV4"}, "Corolla"),
        ("Saab", "9000", SAAB, "9000"),
        ("Saab", "9-3", SAAB, "9-3"),
        # Saab's 1950s 93 is not the 9-3: exact words only.
        ("Saab", "93", SAAB, None),
        ("Volvo", "V70", VOLVO, "V70"),
        # Volvo type codes: series from the first two digits.
        ("Volvo", "744-883", VOLVO, "740"),
        ("Volvo", "745", VOLVO, "740"),
        ("Volvo", "245-883", VOLVO, "240"),
        ("Volvo", "945-811", VOLVO, "940"),
        ("Volvo", "1421341", VOLVO, "140"),
        ("Volvo", "704", VOLVO, "760"),
        ("Volvo", "965", VOLVO, None),  # 960 is not in this vocabulary
        # Older codes name families of their own.
        ("Volvo", "13134", VOLVO, "Amazon"),
        ("Volvo", "221341", VOLVO, "Amazon"),
        ("Volvo", "1641341", VOLVO, "164"),
        ("Volvo", "18335", VOLVO, "P 1800"),
        ("Volvo", "211341", VOLVO, "Duett"),
        ("Volvo", "343511", VOLVO, "340"),
        ("Volvo", "464-313", VOLVO, "460"),
        ("Volvo", "11134", VOLVO, None),
        # Saab: the model number run together with its engine or trim code.
        ("Saab", "900I", SAAB, "900"),
        ("Saab", "96GL", SAAB, "96"),
        ("Saab", "99L-2SNLHD-2,0-CM", SAAB, "99"),
        ("Saab", "95GL", SAAB, "95"),
        ("Saab", "93B", SAAB, None),
        # 850: 854 saloon, 855 estate; an engine code may follow in several groups.
        ("Volvo", "854-512", VOLVO, "850"),
        ("Volvo", "855", VOLVO, "850"),
        ("Volvo", "244-410-2111", VOLVO, "240"),
        ("Volvo", "8551", VOLVO, "850"),
        ("Volvo", "856", VOLVO, None),
        # BMW: series and engine.
        ("BMW", "320I", BMW, "3 Series"),
        ("BMW", "525TDS", BMW, "5 Series"),
        ("BMW", "118D", BMW, "1 Series"),
        ("BMW", "2002", BMW, None),
        # A door count after a slash is not part of the model.
        ("BMW", "323I/2", BMW, "3 Series"),
        ("BMW", "316I/3", BMW, "3 Series"),
        ("BMW", "323I/22", BMW, None),
        ("BMW", "X3", BMW, "X3"),
        # Mercedes-Benz: class letter, alone or with its engine.
        ("Mercedes-Benz", "C", MERCEDES, "C-Class"),
        ("Mercedes-Benz", "E220CDI", MERCEDES, "E-Class"),
        ("Mercedes-Benz", "ML", MERCEDES, "M-Class"),
        ("Mercedes-Benz", "SLK", MERCEDES, "SLK"),
        ("Mercedes-Benz", "A", MERCEDES, None),  # A-Class not in this vocabulary
        ("Mercedes-Benz", "190", MERCEDES, None),
        # A pattern belongs to its make only.
        ("Volvo", "320I", VOLVO, None),
        ("Peugeot", "C", {"307"}, None),
        ("Volvo", "", VOLVO, None),
    ],
)
def test_pattern_model(manufacturer: str, token: str, vocabulary: set[str], expected: str | None) -> None:
    assert pattern_model(manufacturer, token, vocabulary) == expected


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        # Case, spaces and punctuation aside, with or without the make in front.
        ("Mazda", "MAZDA6", {"6", "3", "CX-5"}, "6"),
        ("Mazda", "MX5", {"MX-5", "6"}, "MX-5"),
        ("Buick", "LE SABRE", {"Lesabre"}, "Lesabre"),
        ("Fiat", "FIAT TIPO", {"Tipo", "500"}, "Tipo"),
        ("Volvo", "EX40", {"XC40", *REVIEWED_MODEL_NAMES["Volvo"]}, "EX40"),
        ("Volvo", "EC40", {"C40", *REVIEWED_MODEL_NAMES["Volvo"]}, "EC40"),
        # A name followed by trim words is that name; the longest name wins.
        ("Volkswagen", "ID.7 TOURER GTX", {"ID.7", "ID.7 Tourer"}, "ID.7 Tourer"),
        ("Volkswagen", "ID.7 GTX", {"ID.7", "ID.7 Tourer"}, "ID.7"),
        ("Volvo", "XC40 RECHARGE", {"XC40"}, "XC40"),
        ("Volvo", "V40 CROSS COUNTRY", {"V40", "V40 Cross Country"}, "V40 Cross Country"),
        ("MG", "MG EHS PLUG-IN HYBRID", {"EHS", "ZS"}, "EHS"),
        # Only whole words: EV30 is not the EV3.
        ("Kia", "EV30", {"EV3"}, None),
        ("Volkswagen", "GOLFPLUS", {"Golf"}, None),
        # AIS brand texts repeat the make, once or in two spellings.
        ("Toyota", "TOYOTA TOYOTA YARIS CROSS", {"Yaris", "Yaris Cross"}, "Yaris Cross"),
        ("Lynk & Co", "LYNK & CO LYNK AND CO 08", {"01", "08"}, "08"),
        ("Polestar", "POLESTAR POLESTAR 4", {"2", "4"}, "4"),
        ("Polestar", "POLESTAR POLESTAR", {"2", "4"}, None),
        # Volvo's 1990s model-year letter: the model is after the plus.
        ("Volvo", "VOLVO V + V40", {"V40", "V70"}, "V40"),
        ("Volvo", "VOLVO S+V70", {"V40", "V70"}, "V70"),
        ("Lotus", "ELISE 111R TOUR+", {"Elise"}, "Elise"),
        # Accents fold, Swedish letters stay.
        ("Lamborghini", "HURACAN", {"Huracán"}, "Huracán"),
        ("Citroën", "CITROEN C1", {"C1"}, "C1"),
        # A separator between digits is part of the name: the 1950s 93 is not the 9-3.
        ("Saab", "93", {"9-3", "9-5", "96"}, None),
        ("Saab", "SAAB 95", {"9-3", "9-5", "96"}, None),
        ("Saab", "9 4 X", {"9-4X"}, "9-4X"),
        ("Saab", "9-3", {"9-3"}, "9-3"),
        # Two families spelled alike name neither.
        ("Citroën", "DS3", {"DS3", "Ds 3"}, None),
        ("Volvo", "", {"XC40"}, None),
        ("Volvo", "EX40", {"XC40"}, None),  # not reviewed, not in TS's vocabulary
    ],
)
def test_model_text_family(manufacturer: str, text: str, vocabulary: set[str], expected: str | None) -> None:
    assert model_text_family(manufacturer, text, vocabulary) == expected


def test_the_words_the_registry_writes_a_make_by_are_skipped_in_front() -> None:
    words = {"VW", "VOLKSWAGEN"}

    assert model_text_family("Volkswagen", "VW BEETLE 1600 CABRIOLET", {"Beetle"}, make_words=words) == "Beetle"
    # "ID" starts the ID family's names: the ID. Polo is not the Polo.
    assert model_text_family("Volkswagen", "VOLKSWAGEN, VW ID. POLO", {"Polo", "ID.3"}, make_words=words) is None
    assert model_text_family(
        "Aston Martin", "ASTON MARTIN DB9", {"DB9"}, make_words={"ASTON", "ASTON MARTIN"}
    ) == "DB9"
    mg = {"M", "M.G.", "MG", "MGB"}
    assert model_text_family("MG", "MG MARVEL R ELECTRIC", {"Marvel R", "Mgb"}, make_words=mg) == "Marvel R"
    assert model_text_family("MG", "MGB GT", {"Mgb", "Mgb Gt"}, make_words=mg) == "Mgb Gt"
    assert model_text_family("Lotus", "EVORA 2+0", {"Evora"}) == "Evora"


@pytest.mark.parametrize(
    ("manufacturer", "text", "expected"),
    [
        ("Ford", "FORD DAW FOCUS", "Focus"),
        ("Ford", "FORD B5Y MONDEO", "Mondeo"),
        ("Ford", "FORD RBT KA", "Ka"),
        ("Ford", "FORD BNP MONDEO 2,0", "Mondeo"),
        # A second name after the model, or a trim word, leaves it open.
        ("Ford", "FORD DM2 FOCUS C-MAX", None),
        ("Ford", "FORD DAW FOCUS GHIA", None),
        # "GR" is short for Grand, not a code.
        ("Chrysler", "CHRYSLER GR VOYAGER 3.3", "Grand Voyager"),
        # The code alone names nothing.
        ("Ford", "FORD DAW", None),
        # "E-" marks the electric model, not a code.
        ("Ford", "FORD E-FOCUS", None),
    ],
)
def test_a_chassis_code_in_front_of_the_model(manufacturer: str, text: str, expected: str | None) -> None:
    vocabulary = {"Focus", "Mondeo", "Ka", "C-max", "Voyager", "Grand Voyager"}
    assert model_text_family(manufacturer, text, vocabulary) == expected


def test_a_code_in_front_can_make_another_car() -> None:
    mercedes = {"SL", "SLC", "SLK"}

    assert model_text_family("Mercedes-Benz", "MERCEDES-BENZ 350 SL", mercedes) == "SL"
    assert model_text_family("Mercedes-Benz", "MERCEDES-BENZ 230 SLK", mercedes) == "SLK"
    # The 1970s 450 SLC is TecDoc's SL Coupe (C107), not the 2016 SLC.
    assert model_text_family("Mercedes-Benz", "MERCEDES-BENZ 450 SLC", mercedes) is None


def test_one_spelling_per_name_the_most_used() -> None:
    assert preferred_spellings({"RAV4": 900, "Rav 4": 12, "Corolla": 50}) == {"RAV4", "Corolla"}
    assert preferred_spellings({"Megane": 44972, "Mégane": 3}) == {"Megane"}


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        ("Chrysler", "CHRYSLER GR VOYAGER 3.3", {"Voyager", "Grand Voyager"}, "Grand Voyager"),
        ("Suzuki", "SUZUKI GR VITARA V6 XL.7", {"Vitara", "Grand Vitara"}, "Grand Vitara"),
        ("Toyota", "TOYOTA GR YARIS", {"Yaris"}, None),
        ("Renault", "RENAULT BA - MEGANE", {"Megane"}, "Megane"),
        ("Mazda", "MAZDA BA 323", {"323"}, "323"),
        ("Lexus", "LEXUS IS200", {"IS", "RX"}, "IS"),
        ("Lexus", "LEXUS LEXUS RX300", {"IS", "RX"}, "RX"),
    ],
)
def test_abbreviations_codes_and_lexus(manufacturer: str, text: str, vocabulary: set[str], expected: str | None) -> None:
    assert model_text_family(manufacturer, text, vocabulary) == expected


@pytest.mark.parametrize(
    ("manufacturer", "token", "vocabulary", "expected"),
    [
        # Chassis codes TecDoc gives exactly one family.
        ("Honda", "RD1", {"Cr-v", "Civic"}, "Cr-v"),
        ("Honda", "EU8", {"Cr-v", "Civic"}, "Civic"),
        ("Honda", "CG9", {"Accord"}, "Accord"),
        ("Renault", "BA", {"Megane", "Megane Scenic"}, "Megane"),
        ("Renault", "JA", {"Megane", "Megane Scenic"}, "Megane Scenic"),
        ("Renault", "KC", {"Kangoo"}, "Kangoo"),
        # A code more than one family carries is not in the table.
        ("Ford", "DM2", {"Focus", "C-max", "Kuga"}, None),
        # Reviewed spellings of another family's name.
        ("Pontiac", "TRANS AM", {"Firebird"}, "Firebird"),
        ("Porsche", "BOXTER", {"Boxster"}, "Boxster"),
    ],
)
def test_chassis_codes_and_aliases(manufacturer: str, token: str, vocabulary: set[str], expected: str | None) -> None:
    assert pattern_model(manufacturer, token, vocabulary) == expected


def test_stellantis_e_versions_belong_to_their_family_others_do_not() -> None:
    assert model_text_family("Citroën", "CITROEN E-C3 AIRCROSS", {"C3", "C3 Aircross"}) == "C3 Aircross"
    assert model_text_family("Subaru", "SUBARU SUBARU E-OUTBACK", {"Outback"}) is None
    assert model_text_family("Citroën", "CITROEN 2CV6", {"2 Cv"}) == "2 Cv"


def test_an_alias_never_competes_with_a_name_ts_uses() -> None:
    assert model_text_family("Audi", "AUDI S6 4.2", {"A6"}) == "A6"
    # TS has its own "allroad" family: an alias to A6 would only make it ambiguous.
    assert model_text_family("Audi", "AUDI ALLROAD 2.7T", {"A6", "allroad"}) == "allroad"


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        # Volvo's P120 is the Amazon, its 111xx/112xx codes the 1958-65 PV 544.
        ("Volvo", "VOLVO P 120-12134", {"Amazon", "Pv 544"}, "Amazon"),
        ("Volvo", "VOLVO 11134 E", {"Amazon", "Pv 544"}, "Pv 544"),
        ("Volvo", "VOLVO P 1800 S", {"P 1800", "Amazon"}, "P 1800"),
        # Cadillac writes the Deville with its body in front.
        ("Cadillac", "CADILLAC COUPE DE VILLE", {"Deville"}, "Deville"),
        ("Saab", "SAAB 97", {"Sonett", "96"}, "Sonett"),
        ("Fiat", "FIAT X1/9", {"X1/9"}, "X1/9"),
    ],
)
def test_classic_names(manufacturer: str, text: str, vocabulary: set[str], expected: str | None) -> None:
    assert model_text_family(manufacturer, text, vocabulary) == expected


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        # A family named like the make answers only when nothing after it names another.
        ("MINI", "MINI JCW COUNTRYMAN ALL4", {"MINI", "Mini Countryman"}, None),
        ("MINI", "MINI COOPER S CLUBMAN", {"MINI", "Mini Clubman"}, None),
        # After a real model word the first name stands.
        ("Porsche", "PORSCHE 911 CARRERA 4S", {"911", "Carrera"}, "911"),
        # Body words, short trims and engine sizes are not another model.
        ("MINI", "MINI COOPER S CABRIO", {"MINI", "Mini Clubman"}, "MINI"),
        ("Fiat", "FIAT 128 COUPE 1100", {"128", "1100", "Coupe"}, "128"),
        ("Ford", "FORD MUSTANG GT", {"Mustang", "Gt"}, "Mustang"),
        ("Austin", "AUSTIN MINI CLUBMAN", {"Mini"}, "Mini"),
    ],
)
def test_a_text_that_names_two_models_names_neither(
    manufacturer: str, text: str, vocabulary: set[str], expected: str | None
) -> None:
    assert model_text_family(manufacturer, text, vocabulary) == expected


def test_an_electric_car_is_not_the_combustion_family_with_its_own_electric_name() -> None:
    from ingestion.vehicle_model_patterns import fuel_names_another_family

    assert fuel_names_another_family("Ford", "Mustang", "electricity") == "Mustang Mach-e"
    assert fuel_names_another_family("Ford", "Mustang Mach-e", "petrol") == "Mustang"
    assert fuel_names_another_family("Ford", "Mustang", "petrol") is None
    assert fuel_names_another_family("Ford", "Mustang Mach-e", "electricity") is None
    assert fuel_names_another_family("Ford", "Mustang", None) is None


PV_ERA_VOLVO = {"440", "Duett", "Pv 444", "Pv 544", "Amazon", "P 1800", "740"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # After "P"/"PV" a code is the chassis code of its era: P445 is the 1950s Duett.
        ("VOLVO P 44507 M", "Duett"),
        ("VOLVO P-44506", "Duett"),
        ("VOLVO PV 445 DH DUETT", "Duett"),
        ("VOLVO PV-445 GP", "Duett"),
        ("VOLVO P 44403", "Pv 444"),
        ("VOLVO P 544-11134 S", "Pv 544"),
        # Without it, 445 is the 440 of 1988-96; other codes after "P" read as before.
        ("VOLVO 445-183 GL", "440"),
        ("VOLVO P 120-12134", "Amazon"),
        ("VOLVO P 1800 S", "P 1800"),
    ],
)
def test_a_volvo_code_after_p_is_read_in_its_own_era(text: str, expected: str) -> None:
    assert model_text_family("Volvo", text, PV_ERA_VOLVO) == expected


def test_the_pv_era_reading_needs_the_p() -> None:
    assert pattern_model("Volvo", "445", PV_ERA_VOLVO) == "440"
    assert pattern_model("Volvo", "445", PV_ERA_VOLVO, after_p=True) == "Duett"
    assert pattern_model("Volvo", "744-883", PV_ERA_VOLVO, after_p=True) == "740"
    # The era's answer must still be a family TS uses.
    assert pattern_model("Volvo", "445", {"440"}, after_p=True) is None


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        # The Sportage III's generation code, written as the model text.
        ("Kia", "SL", {"Sportage", "Sorento"}, "Sportage"),
        ("Kia", "SLS", {"Sportage", "Sorento"}, "Sportage"),
        ("Kia", "SL SPORTAGE 1,6 EX", {"Sportage", "Sorento"}, "Sportage"),
        # The registry's abbreviations of Fiat names.
        ("Fiat", "FIAT DOBL 1,6", {"500", *REVIEWED_MODEL_NAMES["Fiat"]}, "Doblo"),
        ("Fiat", "FIAT DOB 1,6", {"500", *REVIEWED_MODEL_NAMES["Fiat"]}, "Doblo"),
        ("Fiat", "FIAT COUP 20V TURBO", {"500", *REVIEWED_MODEL_NAMES["Fiat"]}, "Coupe"),
        ("Fiat", "FIAT COUPÉ 2,0", {"500", *REVIEWED_MODEL_NAMES["Fiat"]}, "Coupe"),
        ("Fiat", "FIAT FIAT DOBL", {"500", *REVIEWED_MODEL_NAMES["Fiat"]}, "Doblo"),
        # The 2012-16 CC is its own family; TS files the "PASSAT CC" under the Passat.
        ("Volkswagen", "CC", {"Passat", *REVIEWED_MODEL_NAMES["Volkswagen"]}, "CC"),
        ("Volkswagen", "PASSAT CC", {"Passat", *REVIEWED_MODEL_NAMES["Volkswagen"]}, "Passat"),
        # The make glued to the number.
        ("Mazda", "MAZDA2", {"2", "3", "6", "CX-3", "Mazda3"}, "2"),
    ],
)
def test_reviewed_codes_and_abbreviations(manufacturer: str, text: str, vocabulary: set[str], expected: str) -> None:
    assert model_text_family(manufacturer, text, vocabulary) == expected


def test_a_text_that_is_only_the_name() -> None:
    from ingestion.vehicle_model_patterns import names_only

    assert names_only("Volvo", "EX30", "EX30")
    assert names_only("Volvo", "VOLVO EX30", "EX30")
    assert names_only("Mazda", "MAZDA6", "6")
    assert names_only("Kia", "SL", "Sportage")
    assert not names_only("Volvo", "EX30 TWIN MOTOR", "EX30")
    assert not names_only("Volvo", "EX30", "EX40")


def test_a_narrower_family_is_the_name_with_more_words() -> None:
    from ingestion.vehicle_model_patterns import narrower_family

    assert narrower_family("EX30 Cross Country", "EX30")
    assert narrower_family("Golf Sportsvan", "Golf")
    assert not narrower_family("EX30", "EX30 Cross Country")
    assert not narrower_family("EX30", "EX30")
    assert not narrower_family("Mazda3", "3")
    assert not narrower_family("Grand C-max", "C-max")


@pytest.mark.parametrize(
    ("manufacturer", "family", "texts", "expected"),
    [
        ("Mercedes-Benz", "S-Class", ("MERCEDES-BENZ RAPIDO", "S80"), "RAPIDO"),
        ("Mercedes-Benz", "B-Class", ("MERCEDES-BENZ HYMER B790ML",), "HYMER"),
        ("Mercedes-Benz", "B-Class", ("M B FRANKIA A700 BD",), "FRANKIA"),
        ("Mercedes-Benz", "B-Class", ("MERCEDES B BÜRSTNER T680",), "BURSTNER"),
        ("Renault", "Megane", ("RENAULT ADRIA ACTIVE",), "ADRIA"),
        ("Fiat", "600", ("FIAT ETRUSCO 600 DB",), "ETRUSCO"),
        # The vans a converter builds on stay.
        ("Mercedes-Benz", "Sprinter", ("MERCEDES-BENZ RAPIDO",), None),
        ("Renault", "Master", ("RENAULT RIMOR SWEDEN",), None),
        ("Volkswagen", "Multivan", ("VW WESTFALIA CALIFORNIA",), None),
        # Nothing converted, or a make not reviewed for converters.
        ("Mercedes-Benz", "S-Class", ("MERCEDES-BENZ S 500",), None),
        ("Škoda", "Rapid", ("SKODA RAPIDO",), None),
        ("Dodge", "Challenger", ("DODGE CHALLENGER",), None),
    ],
)
def test_a_converter_keeps_only_the_vans_it_builds_on(
    manufacturer: str, family: str, texts: tuple[str, ...], expected: str | None
) -> None:
    from ingestion.vehicle_model_patterns import converter_names_another_family

    assert converter_names_another_family(manufacturer, family, texts) == expected


def test_a_converter_word_that_spells_the_family_is_the_family(monkeypatch: pytest.MonkeyPatch) -> None:
    from ingestion.vehicle_model_patterns import (
        REVIEWED_CONVERTER_BASES,
        converter_names_another_family,
    )

    monkeypatch.setitem(REVIEWED_CONVERTER_BASES, "Škoda", ())
    assert converter_names_another_family("Škoda", "Rapid", ("SKODA RAPIDO",)) is None
    assert converter_names_another_family("Škoda", "Octavia", ("SKODA RAPIDO",)) == "RAPIDO"


@pytest.mark.parametrize(
    ("family", "texts", "engine", "expected"),
    [
        # A text naming both a 940 and a 960 code: the engine says which.
        ("940", ("VOLVO 944-964",), "B6304F", "960"),
        ("940", ("VOLVO 945-965",), "B6254F", "960"),
        ("960", ("VOLVO 945-965",), "B230FB", "940"),
        ("940", ("VOLVO 944-964",), "B230FB", None),
        ("940", ("VOLVO 945-965",), "B200F", None),
        # A diesel went in both; no engine, or one series named, decides nothing.
        ("940", ("VOLVO 945-965",), "D24TIC", None),
        ("940", ("VOLVO 944-964",), None, None),
        ("960", ("VOLVO 964-965",), "B230F", None),
        ("940", ("VOLVO 944-942",), "B6304F", None),
        ("V70", ("VOLVO 944-964",), "B6304F", None),
    ],
)
def test_a_two_series_volvo_text_takes_the_engines_series(
    family: str, texts: tuple[str, ...], engine: str | None, expected: str | None
) -> None:
    from ingestion.vehicle_model_patterns import engine_names_another_family

    assert engine_names_another_family("Volvo", family, texts, engine) == expected
    assert engine_names_another_family("Saab", family, texts, engine) is None


@pytest.mark.parametrize(
    ("text", "make_words", "expected"),
    [
        # A learned make word is only ever a whole registry word: "BMW525" (the make
        # glued to a model number) is no make in "BMW 525 IX", an E34 525iX.
        ("BMW 525 IX TOURING", {"BMW", "BMW525"}, "5 Series"),
        ("BMW525 IX", {"BMW", "BMW525"}, "5 Series"),
        ("BMW525I", {"BMW", "BMW525I"}, "5 Series"),
        # The 535d's engine "306D5" names no series, nor do the 1950s 507 and 700.
        ("BMW 306 D5", {"BMW"}, None),
        ("BMW 507", {"BMW"}, None),
        ("BMW 700 LS", {"BMW"}, None),
    ],
)
def test_bmw_make_words_and_series_numbers(text: str, make_words: set[str], expected: str | None) -> None:
    assert model_text_family("BMW", text, {"3 Series", "5 Series", "7 Series", "iX"}, make_words=make_words) == expected


def test_a_learned_make_word_is_a_whole_registry_word() -> None:
    ford = {"E", "Transit", "Focus"}
    words = {"FORD", "FORD-T", "FORD-CNG-TECHNIK"}

    # "FORD T.E.C" is a TEC motorhome, not "FORD-T" with an Econoline "E" after it.
    assert model_text_family("Ford", "FORD T.E.C CARAVAN GMBH", ford, make_words=words) is None
    assert model_text_family("Ford", "FORD-T TRANSIT 2,2", ford, make_words=words) == "Transit"
    # The longer reading of the make wins over the make's own first word.
    assert model_text_family("Ford", "FORD-CNG-TECHNIK FOCUS", ford, make_words=words) == "Focus"
    assert model_text_family("Volkswagen", "VOLKSWAGEN-VW GOLF", {"Golf"}, make_words={"VOLKSWAGEN-VW"}) == "Golf"
    # Over several registry words a make word ends where one does; within the first
    # it may end anywhere.
    mercedes = {"SL", "C-Class"}
    assert model_text_family("Mercedes-Benz", "MERC BENZ 350 SL", mercedes, make_words={"MERC-BENZ"}) == "SL"
    assert model_text_family("Volkswagen", "VW-GOLF CABRIOLET", {"Golf"}, make_words={"VW"}) == "Golf"
    assert model_text_family("Chevrolet", "CHEV.ASTRO VANRCM", {"Astro"}, make_words={"CHEV"}) == "Astro"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # "MERCEDES B" and "M B" abbreviate Mercedes-Benz: no B-Class.
        ("MERCEDES B E240 AVANTGAR", "E-Class"),
        ("MERCEDES B C220CDI", "C-Class"),
        ("MERCEDES B 320 SL", "SL"),
        ("MERCEDES B VIVANO MARCO", None),
        ("M B 250 CE", None),
        # Spelled out, the B is the B-Class.
        ("MERCEDES-BENZ B 180 CDI", "B-Class"),
        ("MERCEDES BENZ B 200", "B-Class"),
    ],
)
def test_mercedes_b_abbreviates_the_make(text: str, expected: str | None) -> None:
    mercedes = {"B-Class", "C-Class", "E-Class", "SL", "Viano"}

    assert model_text_family("Mercedes-Benz", text, mercedes, make_words={"MERCEDES", "MB"}) == expected


@pytest.mark.parametrize(
    ("manufacturer", "text", "vocabulary", "expected"),
    [
        # An engine size is no model: "MAZDA 2.3 KOMBI" is a Mazda 6 2.3.
        ("Mazda", "MAZDA 2.3 KOMBI SPORT E", {"2", "6", "MX-5"}, None),
        ("Mazda", "MAZDA 6 2,0 KOMBI", {"2", "6", "MX-5"}, "6"),
        # Unless the number spells a name: "SAAB 9.3" is the 9-3.
        ("Saab", "SAAB 9.3", {"9-3", "9-5", "93"}, "9-3"),
        ("Saab", "SAAB 9,5 T", {"9-3", "9-5", "93"}, "9-5"),
        # Typing errors for the MX-5.
        ("Mazda", "MAZDA MZ-5", {"2", "5", "MX-5"}, "MX-5"),
        ("Mazda", "MAZDA 5X-5", {"2", "5", "MX-5"}, "MX-5"),
        # An engine size takes its letters along: "4,2Q" leaves no "Q" behind.
        ("Audi", "AUDI DZ A8 4,2Q", {"A8", "Coupe"}, "A8"),
        ("Mazda", "MAZDA 929 3,0I", {"3", "6"}, None),
        # A type code with a point is none.
        ("Volvo", "VOLVO 945.211", {"940", "850"}, "940"),
        # A body word after a code reads as the family of that name, Audi's Coupe for
        # the S2 Coupé of 1990-95 as for an S5 coupé of 2008: the family's reviewed
        # years tell them apart (`vehicle_core_rules.REVIEWED_FAMILY_ERAS`).
        ("Audi", "AUDI S2 COUPE", {"A4", "Coupe", "Cabriolet"}, "Coupe"),
        ("Audi", "AUDI S5 COUPE 4,2Q", {"A5", "Coupe", "Cabriolet"}, "Coupe"),
        ("Alfa Romeo", "ALFA ROMEO 916 SPIDER", {"Spider", "GTV"}, "Spider"),
        # A make word that is also a family, first in the text, is the make.
        ("Chevrolet", "CHEVY CAPRICE CLASSIC", {"Chevy", "Caprice"}, None),
        ("Chevrolet", "CHEVY ASTRO", {"Chevy", "Astro"}, None),
        ("Chevrolet", "CHEVY II", {"Chevy", "Caprice"}, "Chevy"),
        ("Chevrolet", "CHEVROLET CHEVELLE MALIBU", {"Chevelle", "Malibu"}, "Chevelle"),
        # Two Volvo series in one text name neither.
        ("Volvo", "VOLVO 944-964", {"940", "960"}, None),
        ("Volvo", "VOLVO 745-765", {"740", "760"}, None),
        ("Volvo", "VOLVO 945 965 3,0", {"940", "960"}, None),
        ("Volvo", "VOLVO 964-965", {"940", "960"}, "960"),
        ("Volvo", "VOLVO 704-764", {"740", "760"}, "760"),
        ("Volvo", "VOLVO 744-883 GL", {"740", "760"}, "740"),
        # A number after the code is its engine or version, even one that looks like a
        # type code: "944-855" is a four-cylinder 940, no 850.
        ("Volvo", "VOLVO 944-855", {"850", "940", "960"}, "940"),
        ("Volvo", "VOLVO 744-762", {"740", "760"}, None),
        # The 4CV is no Renault 4.
        ("Renault", "RENAULT 4 CV R 1062", {"4", "Clio"}, None),
        ("Renault", "RENAULT R10624CV SPORT", {"4", "Clio"}, None),
        ("Renault", "RENAULT 4 R 1062", {"4", "Clio"}, None),
        ("Renault", "RENAULT 4 L", {"4", "Clio"}, "4"),
        ("Renault", "RENAULT R 4 1123", {"4", "Clio"}, "4"),
    ],
)
def test_readings_that_name_no_family(
    manufacturer: str, text: str, vocabulary: set[str], expected: str | None
) -> None:
    assert model_text_family(manufacturer, text, vocabulary, make_words={"CHEVY", "CHEVELLE"}) == expected


def test_two_volvo_series_name_no_pattern() -> None:
    volvo = {"740", "760", "940", "960"}

    assert pattern_model("Volvo", "944-964", volvo) is None
    assert pattern_model("Volvo", "745-765", volvo) is None
    assert pattern_model("Volvo", "945-965", volvo) is None
    assert pattern_model("Volvo", "945-811", volvo) == "940"
    assert pattern_model("Volvo", "944-855", volvo | {"850"}) == "940"
    assert pattern_model("Volvo", "964-965", volvo) == "960"


def test_names_ts_files_together_and_names_on_hold() -> None:
    from ingestion.vehicle_model_patterns import same_family_names, tolerated_names

    assert same_family_names("BMW", "X1") == ("X1", "iX1")
    assert same_family_names("BMW", "iX3") == ("iX3", "X3")
    assert same_family_names("BMW", "X5") == ("X5",)
    # On hold, a Multivan fill tolerates a CARAVELLE text; not the other way round.
    assert tolerated_names("Volkswagen", "Multivan") == ("Multivan", "Caravelle")
    assert tolerated_names("Volkswagen", "Caravelle") == ("Caravelle",)
    assert tolerated_names("Renault", "Megane") == ("Megane", "Scenic")
    assert tolerated_names("Lada", "4x4") == ("4x4", "Niva")


def test_a_car_ts_has_no_family_for() -> None:
    from ingestion.vehicle_model_patterns import other_car_named

    assert other_car_named("Renault", ("RENAULT 4 CV R 1062",)) == "4CV"
    assert other_car_named("Renault", ("RENAULT", "R 1062 4 CV SPORT")) == "4CV"
    assert other_car_named("Renault", ("RENAULT CV 4 DE LUXE",)) == "4CV"
    assert other_car_named("Renault", ("RENAULT 4 L", "RENAULT R 4 1123")) is None
    assert other_car_named("Fiat", ("FIAT 4 CV",)) is None
