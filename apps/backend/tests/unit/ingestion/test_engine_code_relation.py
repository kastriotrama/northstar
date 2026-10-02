"""Engine-code rules of the matcher: spellings, families, reviewed aliases, lists, EV scope.

Each test names the registry and TecDoc codes the rule was measured on. The
negative tests are the pairs reviewers ruled must stay different engines.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ingestion.fuzzy_matching import (
    FuzzyCandidateMatch,
    FuzzyMatchConfig,
    FuzzyVehicleMatcher,
    ManufacturerCandidateIndex,
    VehicleCandidate,
    VehicleMatchQuery,
    _normalized_code,
    engine_code_families,
    engine_code_family,
    engine_code_forms,
    engine_code_parts,
)
from ingestion.tecdoc.engine_code_aliases import (
    REVIEWED_ENGINE_CODE_ALIASES,
    REVIEWED_SHARED_REGISTRY_NAMES,
    alias_completeness_gaps,
    engine_code_aliases,
    engines_sharing_registry_name,
    maker_key,
)
from ingestion.tecdoc.match_run_adapters import _engine_confirms

_CONFIG = FuzzyMatchConfig()


def _ktype(
    reference: str, manufacturer: str, model: str, *codes: str, **fields: Any
) -> VehicleCandidate:
    return VehicleCandidate(
        reference, manufacturer, model, engine_codes=frozenset(codes), **fields
    )


def _score(
    car_engine: str, candidate: VehicleCandidate, *others: VehicleCandidate, **query: Any
) -> FuzzyCandidateMatch:
    """Score one car against `candidate` in a catalog that also holds `others`."""

    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((candidate, *others)))
    return matcher._score(
        VehicleMatchQuery(
            candidate.model, manufacturer=candidate.manufacturer, engine_code=car_engine, **query
        ),
        candidate,
    )


def _engine_fields(score: FuzzyCandidateMatch) -> set[str]:
    """What the engine branch recorded, with a conflict spelled out."""

    fields = {field for field in score.matched_fields if field.startswith("engine_code")}
    fields |= {field for field in score.missing_fields if field.startswith("engine_code")}
    if "engine_code" in score.conflicting_fields:
        fields.add("conflict")
    return fields


def _legacy_engine_fields(car_engine: str, candidate: VehicleCandidate, known: set[str]) -> set[str]:
    """The engine branch as it was before lists were read: the whole value is one code."""

    query_forms = engine_code_forms(car_engine)
    candidate_forms: set[str] = set()
    candidate_families: set[str] = set()
    for code in candidate.engine_codes:
        candidate_forms |= engine_code_forms(code)
        candidate_families.add(engine_code_family(code) or _normalized_code(code))
    query_family = engine_code_family(car_engine) or _normalized_code(car_engine)
    if query_forms & candidate_forms:
        return {"engine_code"}
    if query_family in candidate_forms or candidate_families & query_forms:
        return {"engine_code_family"}
    family = engine_code_family(car_engine, revision=False)
    if query_forms & known or (family is not None and family in known):
        return {"conflict"}
    return {"engine_code_unverified"}


def _confirms(score: FuzzyCandidateMatch) -> bool:
    """Whether this top candidate would settle a candidate-only KType."""

    return _engine_confirms(
        SimpleNamespace(engine_code_observed=True), SimpleNamespace(candidates=[score])  # type: ignore[arg-type]
    )


# --- 1. BMW technical-update marker ----------------------------------------------


@pytest.mark.parametrize(
    ("registry", "unmarked"),
    [
        ("M52-TUB20", "M52B20"),
        ("M47-TU2D20", "M47D20"),
        ("M57-TUD30", "M57D30"),
        ("M62-TUB44", "M62B44"),
        ("M43-TUB19", "M43B19"),
    ],
)
def test_bmw_technical_update_marker_is_stripped(registry: str, unmarked: str) -> None:
    assert unmarked in engine_code_forms(registry, "BMW")
    # The written form stays a form, and no other maker's code is touched.
    assert _normalized_code(registry) in engine_code_forms(registry, "BMW")
    assert unmarked not in engine_code_forms(registry, "Land Rover")
    assert unmarked not in engine_code_forms(registry)


def test_a_trailing_technical_update_marker_is_left_alone() -> None:
    # N63-B44TU3 against TecDoc's `N63 B44 D` is another notation: stays review.
    assert engine_code_forms("N63-B44TU3", "BMW") == {"N63B44TU3"}


def test_a_technical_update_code_matches_the_ktype_written_without_the_marker() -> None:
    e91 = _ktype("e91", "BMW", "3 Touring (E91)", "M57 D30 (306D3)")
    other = _ktype("e46", "BMW", "3 Touring (E46)", "M47 D20 (204D4)")

    assert _engine_fields(_score("M57-TUD30", e91, other)) == {"engine_code"}
    assert _engine_fields(_score("M57-TUD30", other, e91)) == {"conflict"}


def _e36_and_e46(**e46_fields: Any) -> tuple[VehicleCandidate, VehicleCandidate]:
    e36 = _ktype(
        "e36", "BMW", "3 Touring (E36)", "M52 B20 (206S3)", month_from=199501, month_to=199904
    )
    e46 = _ktype(
        "e46", "BMW", "3 Touring (E46)", "M52 B20 (206S4)",
        **({"month_from": 199803, "month_to": 200301} | e46_fields),
    )
    return e36, e46


def test_a_ktype_with_only_the_replaced_type_code_is_family_for_a_technical_update_car() -> None:
    e36, e46 = _e36_and_e46()

    replaced = _score("M52-TUB20", e36, e46)
    updated = _score("M52-TUB20", e46, e36)

    assert _engine_fields(replaced) == {"engine_code_family"}
    assert _engine_fields(updated) == {"engine_code"}
    assert not _confirms(replaced)
    # The KType with the updated type code leads by more than the automatic margin.
    assert updated.separation_score - replaced.separation_score > _CONFIG.automatic_margin


def test_a_ktype_listing_both_type_codes_is_exact_for_a_technical_update_car() -> None:
    e36, e46 = _e36_and_e46()
    e39 = _ktype(
        "e39", "BMW", "5 (E39)", "M52 B20 (206S3)", "M52 B20 (206S4)",
        month_from=199601, month_to=200306,
    )

    assert _engine_fields(_score("M52-TUB20", e39, e36, e46)) == {"engine_code"}


def test_the_replaced_type_code_is_family_whatever_the_model_and_production_period() -> None:
    # The sibling with the later code was built after this KType ended.
    e36, later = _e36_and_e46(month_from=200001, month_to=200301)
    replaced = _score("M52-TUB20", e36, later)
    assert _engine_fields(replaced) == {"engine_code_family"}
    assert not _confirms(replaced)
    # The later code sits in another model family (5 Series), not among this car's siblings.
    e36, _ = _e36_and_e46()
    e39 = _ktype("e39", "BMW", "5 (E39)", "M52 B20 (206S4)", month_from=199601, month_to=200306)
    assert _engine_fields(_score("M52-TUB20", e36, e39)) == {"engine_code_family"}
    assert _engine_fields(_score("M52-TUB20", e39, e36)) == {"engine_code"}


def test_a_head_with_one_type_code_in_the_catalog_has_no_replaced_type() -> None:
    # M43 B19 has one type code: nothing tells its update apart, so it is the exact engine.
    alone = _ktype("e46-316", "BMW", "3 (E46)", "M43 B19 (194E1)")
    assert _engine_fields(_score("M43-TUB19", alone)) == {"engine_code"}
    # So is a KType writing the head without a type code.
    bare = _ktype("bare", "BMW", "3 (E46)", "M52 B20")
    assert _engine_fields(_score("M52-TUB20", bare, *_e36_and_e46())) == {"engine_code"}


def test_the_replaced_type_is_decided_per_engine_head_across_the_catalog() -> None:
    # The X6 lists M57 D30 only as 306D3 and 306D5: both updated engines. The
    # lowest type code of its own model family is not the replaced one (306D1).
    x6_30d = _ktype("x6-30d", "BMW", "X6 (E71, E72)", "M57 D30 (306D3)")
    x6_35d = _ktype("x6-35d", "BMW", "X6 (E71, E72)", "M57 D30 (306D5)")
    e39 = _ktype("e39", "BMW", "5 (E39)", "M57 D30 (306D1)", month_from=199808, month_to=200009)
    x5 = _ktype("x5", "BMW", "X5 (E53)", "M57 D30 (306D1)", "M57 D30 (306D2)")

    for car in ("M57-TU2D30", "M57-TUD30"):
        assert _engine_fields(_score(car, x6_30d, x6_35d, e39, x5)) == {"engine_code"}
        assert _engine_fields(_score(car, x6_35d, x6_30d, e39, x5)) == {"engine_code"}
        # Only the replaced code: family, with no updated sibling in its model or period.
        replaced = _score(car, e39, x6_30d, x6_35d, x5)
        assert _engine_fields(replaced) == {"engine_code_family"}
        assert not _confirms(replaced)
        assert _engine_fields(_score(car, x5, x6_30d, x6_35d, e39)) == {"engine_code"}
    # Another maker's lower type code does not decide BMW's replaced type.
    alpina = _ktype("alpina", "Alpina", "D10 (E39)", "M57 D30 (306D0)")
    assert _engine_fields(_score("M57-TU2D30", e39, x6_30d, x5, alpina)) == {"engine_code_family"}


def test_a_code_without_the_marker_matches_the_replaced_type_code_exactly() -> None:
    e36, e46 = _e36_and_e46()

    assert _engine_fields(_score("M52-B20", e36, e46)) == {"engine_code"}


# --- 2. Spelling forms -------------------------------------------------------------


@pytest.mark.parametrize(("registry", "catalog"), [("B205E/B", "B205E"), ("B235E/B", "B235E")])
def test_saab_one_letter_slash_suffix_names_the_base_code(registry: str, catalog: str) -> None:
    saab = _ktype("k", "Saab", "9-5 Estate (YS3E)", catalog)

    score = _score(registry, saab)

    assert _engine_fields(score) == {"engine_code"}
    assert _confirms(score)


def test_a_slash_is_not_split_outside_the_saab_suffix() -> None:
    # Citroën `M25/659` is one code; so is a Saab code with more than one letter after the slash.
    assert engine_code_forms("M25/659", "Citroën") == {"M25659"}
    assert engine_code_forms("B205E/BX", "Saab") == {"B205EBX"}
    # The Saab rule is Saab's alone.
    assert engine_code_forms("B205E/B", "Opel") == {"B205EB"}
    opel = _ktype("k", "Opel", "Vectra C", "B205E")
    assert _engine_fields(_score("B205E/B", opel)) == {"engine_code_unverified"}


@pytest.mark.parametrize(
    ("registry", "catalog"),
    [
        ("M139.580", "139.580"),
        ("M282.814", "282.814"),
        ("OM656.830", "656.830"),
        ("E780.998", "EM 780.998"),
        ("EM780.600", "780.600"),
        ("E780.200", "EM 780.200"),
        ("M 274.910", "M 274.910"),
    ],
)
def test_mercedes_engine_number_matches_whatever_letters_precede_it(
    registry: str, catalog: str
) -> None:
    mercedes = _ktype("k", "MERCEDES-BENZ", "C-CLASS (W206)", catalog)

    assert _engine_fields(_score(registry, mercedes)) == {"engine_code"}


def test_the_mercedes_number_form_is_mercedes_only_and_makes_no_family() -> None:
    assert engine_code_forms("M139.580", "MERCEDES-BENZ") == {"M139580", "139580"}
    assert engine_code_forms("M139.580", "Smart") == {"M139580"}
    assert engine_code_forms("M139.580") == {"M139580"}
    other_maker = _ktype("k", "Infiniti", "Q30", "139.580")
    assert _engine_fields(_score("M139.580", other_maker)) == {"engine_code_unverified"}
    # 642.955 and 642.950 are different engines: no `OM 642` family joins them.
    assert engine_code_families("OM642.955", "MERCEDES-BENZ") == frozenset()
    s_class = _ktype("k", "MERCEDES-BENZ", "S-CLASS (W222)", "OM 642.950")
    carrier = _ktype("o", "MERCEDES-BENZ", "E-CLASS (W212)", "OM 642.955")
    assert _engine_fields(_score("OM642.955", s_class, carrier)) == {"conflict"}


def test_porsche_registry_form_is_not_normalized() -> None:
    # `MDK.DA` = `DKDA` was left out: the same registry code sits on a 718 Boxster
    # whose KType lists DKDC/DKDD, so the equivalence is unproven.
    assert engine_code_forms("MDK.DA", "Porsche") == {"MDKDA"}
    cayman = _ktype("k", "Porsche", "718 CAYMAN (982)", "DKDA", "DWAA")
    assert _engine_fields(_score("MDK.DA", cayman)) == {"engine_code_unverified"}


# --- 3. Family against variant: family level only --------------------------------


@pytest.mark.parametrize(
    ("maker", "car", "catalog"),
    [
        ("Subaru", "EJ253", "EJ25"),
        ("Subaru", "EJ251", "EJ25"),
        ("Subaru", "EJ254", "EJ25"),
        ("Subaru", "EJ25", "EJ257"),
        ("Subaru", "EJ20", "EJ204"),
        ("Nissan", "HR13", "HR13DDT"),
        ("Nissan", "HR16", "HR16DE"),
        ("Nissan", "QG18", "QG18DE"),
        ("Nissan", "YD22", "YD22DDTi"),
        ("Honda", "L15B", "L15B3"),
        ("Honda", "B20B3", "B20B"),
        ("Volvo", "B18", "B 18 D"),
        ("Volvo", "B20", "B 20 E"),
        ("Suzuki", "H25", "H 25 A"),
        ("BMW", "N42-B18", "N42 B18 A"),
        ("BMW", "M70-B50M", "M70 B50 (5012A)"),
        ("BMW", "M20-B20L", "M20 B20 (206KA)"),
    ],
)
def test_a_family_and_its_variant_are_family_evidence_never_exact(
    maker: str, car: str, catalog: str
) -> None:
    ktype = _ktype("k", maker, "Model", catalog)

    family = _score(car, ktype)
    exact = _score(catalog, ktype)

    assert _engine_fields(family) == {"engine_code_family"}
    assert _engine_fields(exact) == {"engine_code"}
    assert not family.conflicting_fields
    assert not _confirms(family)
    assert exact.separation_score - family.separation_score > _CONFIG.automatic_margin


@pytest.mark.parametrize(
    ("maker", "car", "catalog"),
    [
        # The pairs reviewers ruled different engines.
        ("Volvo", "B230F", "B 230 FD"),
        ("Volvo", "B230FD", "B 230 F"),
        ("Volvo", "B230A", "B 230 K"),
        ("Opel", "16S", "16 SH"),
        ("Opel", "16SH", "16 S"),
        ("Renault", "D4F-742", "D4F 740"),
        ("Renault", "K9K-636", "K9K 646"),
        ("Renault", "K9K 646", "K9K 636"),
        ("Renault", "K4M-766", "K4M 760"),
        ("Renault", "H5F-400", "H5F 408"),
        ("BMW", "B48-B20V", "B48 B16 P"),
        ("BMW", "B48-B20V", "B48 B20 A"),
        ("BMW", "B48-B20A", "B48 B20 B"),
        ("BMW", "B38-A15M", "B38 A15 A"),
        # Two variants of one reviewed grammar family.
        ("Subaru", "EJ251", "EJ253"),
        ("Nissan", "SR20DE", "SR20DET"),
        ("Honda", "N16A1", "N16A3"),
        ("Volvo", "B4204T2", "B 4204 T26"),
        ("Volvo", "B5254T", "B 5254 T6"),
    ],
)
def test_two_variants_of_one_family_stay_different_engines(
    maker: str, car: str, catalog: str
) -> None:
    ktype = _ktype("k", maker, "Model", catalog)
    carrier = _ktype("o", maker, "Other", car)

    score = _score(car, ktype, carrier)

    assert _engine_fields(score) == {"conflict"}


def test_variant_grammars_are_scoped_to_their_maker() -> None:
    assert engine_code_families("EJ253", "Subaru") == {"EJ25"}
    assert engine_code_families("EJ253", "Saab") == frozenset()
    assert engine_code_families("HR13DDT", "Nissan") == {"HR13"}
    assert engine_code_families("HR13DDT", "Renault") == frozenset()
    assert engine_code_families("L15B3", "Honda") == {"L15B"}
    assert engine_code_families("F16D3", "Chevrolet") == frozenset()
    saab = _ktype("k", "Saab", "9-2X", "EJ25")
    assert _engine_fields(_score("EJ253", saab)) == {"engine_code_unverified"}


def test_multi_token_revision_family_keeps_the_first_token_family() -> None:
    assert engine_code_families("N42 B18 A", "BMW") == {"N42", "N42B18"}
    assert engine_code_families("M70-B50M") == {"M70", "M70B50"}
    assert engine_code_families("B 18 D") == {"B18"}
    assert engine_code_families("K9K 636") == {"K9K"}
    # Too short, all digits, or not one letter after a digit: no family.
    assert engine_code_families("16 S") == frozenset()
    assert engine_code_families("B 230 FD") == frozenset()
    assert engine_code_families("C 20 NE") == frozenset()
    # `engine_code_family` itself is unchanged: knowledge of an engine still rests on it.
    assert engine_code_family("B 18 D") is None
    assert engine_code_family("N42 B18 A", revision=False) == "N42"


def test_a_variant_no_ktype_carries_stays_unverified_beside_another_variant() -> None:
    # EJ254 is in no KType; against a KType with EJ253 it neither matches nor contradicts.
    legacy = _ktype("k", "Subaru", "LEGACY IV (BL)", "EJ253")

    assert _engine_fields(_score("EJ254", legacy)) == {"engine_code_unverified"}


def test_an_exact_variant_clears_the_margin_over_the_bare_family_ktype() -> None:
    bare = _ktype("bare", "Subaru", "Legacy", "EJ25")
    exact = _ktype("exact", "Subaru", "Legacy", "EJ253")
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((bare, exact)))

    result = matcher.match(VehicleMatchQuery("Legacy", manufacturer="Subaru", engine_code="EJ253"))

    assert [c.candidate_reference for c in result.candidates] == ["exact", "bare"]
    assert result.eligible_for_auto_resolution is True


# --- 4. Reviewed aliases -------------------------------------------------------------


def test_alias_table_is_maker_scoped_complete_and_unambiguous() -> None:
    seen: dict[tuple[str, str], frozenset[str]] = {}
    for entry in REVIEWED_ENGINE_CODE_ALIASES:
        assert entry.makers and len(entry.names) >= 2 and entry.evidence
        assert all(name == _normalized_code(name) for name in entry.names)
        for maker in entry.makers:
            assert maker == maker_key(maker)
            for name in entry.names:
                # One name, one engine: an ambiguous name would join two entries.
                assert (maker, name) not in seen
                seen[(maker, name)] = entry.names
    names = {name for entry in REVIEWED_ENGINE_CODE_ALIASES for name in entry.names}
    # Left out on purpose: the Spark pair, the PSA bracket name, Porsche, GM's other 1.8 codes.
    assert not names & {"B12D1", "LMU", "ZLC", "ZKZ", "DKDA", "MDKDA", "LUW", "LWE"}
    # A registry name two engines share is family evidence: never also an exact alias.
    assert not names & {name for entry in REVIEWED_SHARED_REGISTRY_NAMES for name in entry.names}
    assert engine_code_aliases("MINI", "B38A15M") == {"B38A15A", "B36A15A"}
    # Every catalog name of the Captiva / Cruze 110 kW diesel is in one entry.
    assert engine_code_aliases("CHEVROLET", "Z20DMH") == {"LLW", "Z20S"}
    assert engine_code_aliases("CHEVROLET", "A18XER") == {"F18D4", "2H0"}
    assert engine_code_aliases("OPEL", "HNS") == {"EB2ADTS", "F12XHT", "D12XHT"}
    assert engine_code_aliases("OPEL", "A14XER") == frozenset()


@pytest.mark.parametrize(
    ("maker", "car", "catalog"),
    [
        ("Chevrolet", "A14XER", ("LDD",)),
        ("Chevrolet", "A12XER", ("LDC",)),
        ("Chevrolet", "A13DTE", ("LSF",)),
        ("Chevrolet", "A16XER", ("LDE",)),
        ("Chevrolet", "A16XER", ("F16D4", "LDE")),
        ("Chevrolet", "A17DTF", ("LUD",)),
        ("Chevrolet", "A22DMH", ("LNQ",)),
        ("Chevrolet", "A14NET", ("LUJ",)),
        ("Chevrolet", "A18XER", ("2H0",)),
        ("Chevrolet", "A18XER", ("2H0", "F18D4")),
        ("Chevrolet", "Z20DMH", ("Z 20 S",)),
        ("Chevrolet", "Z20DMH", ("LLW",)),
        ("Opel", "HNS", ("F 12 XHT (EB2ADTS)",)),
        ("Vauxhall", "HNS", ("D 12 XHT (EB2ADTS)",)),
        ("Toyota", "HNS", ("EB2ADTS",)),
        ("Peugeot", "8FP", ("8FS (EP3)", "EP3", "EP3C")),
        ("BMW", "S62-B49", ("S62 B50 (508S1)",)),
        ("MINI", "B38-A15M", ("B38 A15 A", "B38 A15 F")),
        ("MINI", "B38-A15M", ("B36 A15 A",)),
        ("MINI", "B38-A15A", ("B36 A15 A",)),
    ],
)
def test_a_reviewed_alias_scores_as_the_exact_engine_but_never_confirms(
    maker: str, car: str, catalog: tuple[str, ...]
) -> None:
    ktype = _ktype("k", maker, "Model", *catalog)
    # The registry name is a catalog code elsewhere, so without the alias it conflicts.
    carrier = _ktype("o", maker, "Other", car)

    alias = _score(car, ktype, carrier)
    exact = _score(next(iter(sorted(catalog))), ktype, carrier)

    assert _engine_fields(alias) == {"engine_code_alias"}
    assert alias.separation_score == exact.separation_score
    # An alias alone never settles which engine a candidate-only KType has.
    assert "engine_code" not in alias.matched_fields
    assert not _confirms(alias)


def test_both_catalog_names_of_one_engine_score_alike() -> None:
    # A one-sided alias would prefer the `Z 20 S` Captiva over its `LLW` sibling.
    z20s = _ktype("z20s", "Chevrolet", "CAPTIVA (C100, C140)", "Z 20 S")
    llw = _ktype("llw", "Chevrolet", "CAPTIVA (C100, C140)", "LLW")
    carrier = _ktype("o", "Chevrolet", "CRUZE (J300)", "LLW", "Z 20 DMH")

    assert (
        _score("Z20DMH", z20s, llw, carrier).separation_score
        == _score("Z20DMH", llw, z20s, carrier).separation_score
    )
    # TecDoc's own co-listing is the plain exact engine.
    assert _engine_fields(_score("Z20DMH", carrier, z20s, llw)) == {"engine_code"}


def test_both_mini_cooper_ktypes_score_alike_for_either_registry_name() -> None:
    # A one-sided alias preferred the front-wheel-drive Clubman over its ALL4 sibling.
    fwd = _ktype("fwd", "MINI", "MINI CLUBMAN (F54)", "B36 A15 A", "B38 A15 A")
    all4 = _ktype("all4", "MINI", "MINI CLUBMAN (F54)", "B36 A15 A")
    carrier = _ktype("o", "MINI", "MINI (F56)", "B38 A15 M")

    assert (
        _score("B38-A15M", fwd, all4, carrier).separation_score
        == _score("B38-A15M", all4, fwd, carrier).separation_score
    )
    assert (
        _score("B38-A15A", fwd, all4).separation_score
        == _score("B38-A15A", all4, fwd).separation_score
    )
    # TecDoc's own code stays the exact engine; the reviewed name never confirms.
    assert _engine_fields(_score("B38-A15A", fwd, all4)) == {"engine_code"}
    assert _engine_fields(_score("B38-A15A", all4, fwd)) == {"engine_code_alias"}


# --- 4b. One registry name, two engines ----------------------------------------------


@pytest.mark.parametrize("maker", ["Jaguar", "Land Rover"])
def test_a_registry_name_two_engines_share_is_family_on_both(maker: str) -> None:
    # `204PT` is registered for the Ford-derived 1,999 cc and the Ingenium 1,997 cc petrol.
    ford = _ktype("ford", maker, "Model", "204PT(GTDI)", displacement_cc=1999)
    ingenium = _ktype("ingenium", maker, "Model", "PT204(AJ20P4)", displacement_cc=1997)
    diesel = _ktype("diesel", maker, "Model", "204DTD(AJ20D4)", displacement_cc=1999)

    on_ford = _score("204PT", ford, ingenium, diesel)
    on_ingenium = _score("204PT", ingenium, ford, diesel)

    assert _engine_fields(on_ford) == _engine_fields(on_ingenium) == {"engine_code_family"}
    assert on_ford.separation_score == on_ingenium.separation_score
    assert not _confirms(on_ford) and not _confirms(on_ingenium)
    # Another engine of the maker still contradicts.
    assert _engine_fields(_score("204PT", diesel, ford, ingenium)) == {"conflict"}
    # The registry's `PT204` names the Ingenium only.
    assert _engine_fields(_score("PT204", ingenium, ford)) == {"engine_code"}
    assert _engine_fields(_score("PT204", ford, ingenium)) == {"conflict"}
    assert _confirms(_score("PT204", ingenium, ford))


def test_shared_registry_names_are_maker_scoped() -> None:
    assert engines_sharing_registry_name("JAGUAR", "204PT") == {"204PT", "PT204"}
    assert engines_sharing_registry_name("LANDROVER", "204PT") == {"204PT", "PT204"}
    assert engines_sharing_registry_name("JAGUAR", "PT204") == frozenset()
    assert engines_sharing_registry_name("FORD", "204PT") == frozenset()
    ford = _ktype("k", "Ford", "Model", "204PT(GTDI)")
    ingenium = _ktype("o", "Ford", "Other", "PT204(AJ20P4)")
    assert _engine_fields(_score("204PT", ford, ingenium)) == {"engine_code"}
    assert _engine_fields(_score("204PT", ingenium, ford)) == {"conflict"}


# --- 4c. Every catalog name of a reviewed engine --------------------------------------


def _spec(
    reference: str, maker: str, model: str, cc: int, kw: int, *codes: str, fuel: str = "petrol"
) -> VehicleCandidate:
    return _ktype(
        reference, maker, model, *codes,
        displacement_cc=cc, power_kw=kw, fuels=frozenset({fuel}),
    )


_COMPLETENESS_CATALOG = (
    # MINI: the Clubman ALL4 carries only the name the entry once missed.
    _spec("clubman", "MINI", "MINI CLUBMAN (F54)", 1499, 100, "B36 A15 A", "B38 A15 A"),
    _spec("clubman-all4", "MINI", "MINI CLUBMAN (F54)", 1499, 100, "B36 A15 A"),
    _spec("clubman-one", "MINI", "MINI CLUBMAN (F54)", 1499, 75, "B38 A15 A"),
    # Chevrolet: both names of the Captiva 110 kW diesel are in the entry.
    _spec("captiva-z20s", "Chevrolet", "CAPTIVA (C100, C140)", 1991, 110, "Z 20 S", fuel="diesel"),
    _spec("captiva-llw", "Chevrolet", "CAPTIVA (C100, C140)", 1991, 110, "LLW", fuel="diesel"),
    # The Cruze's `LUW, LWE` KTypes differ in power from the `2H0` one: left out on purpose.
    _spec("cruze-2h0", "Chevrolet", "CRUZE (J300)", 1796, 104, "2H0"),
    _spec("cruze-luw", "Chevrolet", "CRUZE (J300)", 1796, 103, "LUW", "LWE"),
    # Same model and power, other fuel or displacement: another engine.
    _spec("cruze-lpg", "Chevrolet", "CRUZE (J300)", 1796, 104, "XYZ", fuel="lpg"),
    _spec("cruze-16", "Chevrolet", "CRUZE (J300)", 1598, 104, "LXT"),
    # A twin without engine codes says nothing about names.
    _spec("cruze-blank", "Chevrolet", "CRUZE (J300)", 1796, 104),
    # Jaguar: the two engines registered as `204PT`.
    _spec("xe-ford", "Jaguar", "XE (X760)", 1999, 177, "204PT(GTDI)"),
    _spec("xe-ingenium", "Jaguar", "XE (X760)", 1997, 184, "PT204(AJ20P4)"),
    # The same code set under another maker is outside every entry's scope.
    _spec("opel", "Opel", "ASTRA J", 1796, 104, "A 18 XER"),
    _spec("opel-twin", "Opel", "ASTRA J", 1796, 104, "Z 18 XER"),
)


def test_the_reviewed_names_list_every_catalog_name_of_their_engine() -> None:
    assert alias_completeness_gaps(_COMPLETENESS_CATALOG) == ()


def test_the_completeness_check_reports_a_twin_ktype_under_a_missing_name() -> None:
    # The MINI entry as it was first written: the ALL4 twin carries none of its names.
    one_sided = [
        entry.__class__(entry.makers, entry.names - {"B36A15A"}, entry.evidence)
        for entry in REVIEWED_ENGINE_CODE_ALIASES
        if "B36A15A" in entry.names
    ]
    assert len(one_sided) == 1

    gaps = alias_completeness_gaps(_COMPLETENESS_CATALOG, one_sided)

    assert [(gap.candidate_reference, gap.names) for gap in gaps] == [
        ("clubman-all4", frozenset({"B36A15A"}))
    ]
    assert gaps[0].entry is one_sided[0]
    assert (gaps[0].model, gaps[0].displacement_cc, gaps[0].power_kw) == (
        "MINI CLUBMAN (F54)", 1499, 100,
    )
    # A twin of a carrier under an unlisted name is reported for the shared names too.
    twin = _spec("xe-other", "Jaguar", "XE (X760)", 1997, 184, "AJ200P")
    shared = alias_completeness_gaps((*_COMPLETENESS_CATALOG, twin))
    assert [gap.candidate_reference for gap in shared] == ["xe-other"]
    assert shared[0].entry in REVIEWED_SHARED_REGISTRY_NAMES


@pytest.mark.parametrize(
    ("maker", "car", "catalog"),
    [
        # The dropped Spark pair.
        ("Chevrolet", "B12D1", "LMU"),
        # GM's other 1.8 codes are never co-listed with `2H0`: left out of the entry.
        ("Chevrolet", "A18XER", "LUW"),
        ("Chevrolet", "A18XER", "LWE"),
        # Aliases hold in their maker's scope only.
        ("Opel", "A14XER", "LDD"),
        ("BMW", "B38-A15M", "B38 A15 A"),
        ("Renault", "HNS", "EB2ADTS"),
        # A sibling type code of the same PSA family is another engine.
        ("Peugeot", "8FP", "8FR (EP3C)"),
        ("Peugeot", "5FU", "5FY (EP6CDTX)"),
        ("Peugeot", "ZLC", "ZKZ (ZK03)"),
        # Different engines of one Chevrolet displacement.
        ("Chevrolet", "A16XER", "LXT"),
        ("Chevrolet", "A12XER", "LWD"),
    ],
)
def test_names_outside_a_reviewed_entry_stay_different_engines(
    maker: str, car: str, catalog: str
) -> None:
    ktype = _ktype("k", maker, "Model", catalog)
    carrier = _ktype("o", maker, "Other", car)

    assert _engine_fields(_score(car, ktype, carrier)) == {"conflict"}


@pytest.mark.parametrize(
    ("car", "catalog"),
    [("SH-VPTS", "SHY1"), ("SH-VPTS", "SHY4"), ("PY-VPR", "PYY1"), ("PE-VPS", "PEY6"), ("PY-VPR", "PY-Y8")],
)
def test_mazda_type_name_and_tecdoc_variant_share_only_the_family(car: str, catalog: str) -> None:
    mazda = _ktype("k", "Mazda", "CX-5 (KE, GH)", catalog)
    carrier = _ktype("o", "Mazda", "6 Estate (GJ, GL)", car)

    score = _score(car, mazda, carrier)

    assert _engine_fields(score) == {"engine_code_family"}
    assert not _confirms(score)
    # TecDoc listing the type name itself is the exact engine.
    assert _engine_fields(_score(car, carrier, mazda)) == {"engine_code"}


@pytest.mark.parametrize(
    ("maker", "car", "catalog"),
    [
        ("Mazda", "SH-VPTS", "PYY1"),  # another engine family
        ("Mazda", "SH-VPTS", "SH-VPTR"),  # two type names
        ("Mazda", "ZJ-VEM", "ZJ46"),  # not TecDoc's `XXYn` variant form
        ("Kia", "SH-VPTS", "SHY1"),  # the grammar is Mazda's alone
    ],
)
def test_mazda_grammar_does_not_reach_further(maker: str, car: str, catalog: str) -> None:
    ktype = _ktype("k", maker, "Model", catalog)
    carrier = _ktype("o", maker, "Other", car)

    assert _engine_fields(_score(car, ktype, carrier)) == {"conflict"}


# --- 5. Lists of codes in one registry value -----------------------------------------


def test_engine_code_parts_splits_on_commas_only_and_de_duplicates() -> None:
    assert engine_code_parts("HF1002N0, HD1001N0") == ("HF1002N0", "HD1001N0")
    assert engine_code_parts("L2S, L2S") == ("L2S",)
    assert engine_code_parts("H5H-470, H5H 470,H5H-480") == ("H5H-470", "H5H-480")
    assert engine_code_parts("A06/635, A06/664") == ("A06/635", "A06/664")
    assert engine_code_parts("K9K 636") == ("K9K 636",)
    assert engine_code_parts(" , ") == ()
    # A list is no single code: it gets no reviewed spelling and no new family.
    assert engine_code_forms("M52-TUB20, M54-B22", "BMW") == {"M52TUB20M54B22"}
    assert engine_code_families("EJ253, EJ251", "Subaru") == {"EJ253"}  # as engine_code_family


def _electric(reference: str, model: str, *codes: str, **fields: Any) -> VehicleCandidate:
    return _ktype(
        reference, "BMW", model, *codes,
        **({"electrification": "battery_electric", "month_from": 202107} | fields),
    )


def test_a_list_naming_the_whole_electric_drivetrain_is_the_exact_engine() -> None:
    xdrive50 = _electric("x50", "iX (I20)", "HA0001N0", "HA0002N0")
    m70 = _electric("m70", "iX (I20)", "HA0002N0")

    score = _score("HA0002N0, HA0001N0", xdrive50, m70, build_month=202303, year=2023)

    assert _engine_fields(score) == {"engine_code"}
    assert _confirms(score)
    # The single-motor sibling holds only one of the two: the result the value had before.
    partial = _score("HA0002N0, HA0001N0", m70, xdrive50, build_month=202303, year=2023)
    assert _engine_fields(partial) == {"engine_code_family"}
    assert score.separation_score - partial.separation_score > _CONFIG.automatic_margin


def test_a_motor_and_range_extender_list_names_the_whole_drivetrain() -> None:
    rex = _electric(
        "rex", "i3 (I01)", "IB1P25B", "W20K06A",
        electrification="range_extender", month_from=201308,
    )
    electric = _electric("bev", "i3 (I01)", "IB1P25B", month_from=201308)

    score = _score("W20-K06A, IB1P25B", rex, electric, build_month=201706, year=2017)

    assert _engine_fields(score) == {"engine_code"}
    assert _confirms(score)


def test_a_list_of_alternative_combustion_engines_scores_exact_but_never_confirms() -> None:
    duster = _ktype("k", "Dacia", "DUSTER (HM_)", "H5H 470", "H5H 480")

    score = _score("H5H-470, H5H-480", duster, year=2020)
    single = _score("H5H-470", duster, year=2020)

    assert _engine_fields(score) == {"engine_code_list"}
    assert score.separation_score == single.separation_score
    assert "engine_code" not in score.matched_fields
    assert not _confirms(score)
    assert _confirms(single)


@pytest.mark.parametrize(
    ("maker", "car", "catalog", "fields"),
    [
        # Alternatives a type approval covers, even when the KType lists exactly those.
        ("Ford", "LPA, LP1", ("LPA", "LP1"), {"fuels": frozenset({"petrol"})}),
        ("Citroën", "A06/635, A06/664", ("A06/635", "A06/664"), {}),
        ("Porsche", "M28.49, M28.50", ("M 28.49", "M 28.50"), {"electrification": "combustion"}),
        # A plug-in hybrid's engine and motor: left to the plug-in power decision.
        ("BMW", "B48-B20A, GC1P25A", ("B48 B20 A", "GC1P25A"), {"electrification": "plug_in_hybrid"}),
    ],
)
def test_a_list_that_does_not_name_a_whole_electric_drivetrain_never_confirms(
    maker: str, car: str, catalog: tuple[str, ...], fields: dict[str, Any]
) -> None:
    ktype = _ktype("k", maker, "Model", *catalog, year_from=1980, **fields)

    score = _score(car, ktype, year=2020)

    assert _engine_fields(score) == {"engine_code_list"}
    assert not _confirms(score)


def test_an_electric_list_confirms_only_inside_the_production_window() -> None:
    # Tesla reuses motor codes: a Model Y built 2025-10 is not the KType that ended 2025-01.
    performance = _ktype(
        "perf", "Tesla", "MODEL Y", "3D3", "4D2",
        electrification="battery_electric", month_from=202406, month_to=202501,
        year_from=2024, year_to=2025,
    )
    long_range = _ktype(
        "lr", "Tesla", "MODEL Y", "3D3", "4D1",
        electrification="battery_electric", month_from=202109, month_to=202501,
        year_from=2021, year_to=2025,
    )

    inside = _score("3D3, 4D2", performance, long_range, build_month=202409, year=2024)
    outside = _score("3D3, 4D2", performance, long_range, build_month=202510, year=2025)
    before = _score("3D3, 4D2", performance, long_range, build_month=202311, year=2023)
    undated = _score("3D3, 4D2", performance, long_range)

    assert _engine_fields(inside) == {"engine_code"}
    assert _engine_fields(outside) == _engine_fields(before) == {"engine_code_list"}
    assert _engine_fields(undated) == {"engine_code_list"}
    # Without a build month the year decides.
    assert _engine_fields(_score("3D3, 4D2", performance, long_range, year=2024)) == {"engine_code"}
    assert _engine_fields(_score("3D3, 4D2", performance, long_range, year=2026)) == {
        "engine_code_list"
    }


def test_codes_on_every_ktype_of_the_model_are_not_distinctive() -> None:
    # Every KType of this model carries L2S: the code cannot say which one the car is.
    d85 = _ktype("85d", "Tesla", "MODEL S", "L2S", electrification="battery_electric", year_from=2014)
    p85 = _ktype("p85d", "Tesla", "MODEL S", "L2S", electrification="battery_electric", year_from=2014)

    shared = _score("L2S, L2S", d85, p85, year=2016)

    assert _engine_fields(shared) == {"engine_code_list"}
    assert not _confirms(shared)
    # Once a sibling KType has another motor, the code tells them apart.
    rwd = _ktype("85", "Tesla", "MODEL S", "L1S", electrification="battery_electric", year_from=2012)
    assert _engine_fields(_score("L2S, L2S", d85, p85, rwd, year=2016)) == {"engine_code"}
    # A dual-motor list is distinctive when only one of its motors is on every KType.
    gtx = _ktype("gtx", "Volkswagen", "ID.5 (E39)", "EBJA", "EBRA", fuels=frozenset({"electric"}), year_from=2021)
    pro = _ktype("pro", "Volkswagen", "ID.5 (E39)", "EBJA", fuels=frozenset({"electric"}), year_from=2021)
    assert _engine_fields(_score("EBJA, EBRA", gtx, pro, year=2023)) == {"engine_code"}


@pytest.mark.parametrize(
    ("maker", "car", "catalog", "carriers"),
    [
        # Kia EV9: one listed motor is not on the KType. A real conflict, never softened.
        ("Kia", "EM18, EM16", ("EM16",), ("EM18",)),
        # BMW 530e against the 520i mild hybrid: the plug-in motor is not the 48 V one.
        ("BMW", "B48-B20A, GC1P25A", ("B48 B20 A", "JA1"), ("GC1",)),
        ("BMW", "B47-D20B, JA1S03M0", ("B47 D20 B", "JA1"), ()),
        # Toyota RAV4 hybrid: engine plus a motor code TecDoc does not list.
        ("Toyota", "A25A-FXS, 1VM", ("A25A-FXS",), ()),
        # One code differs by a revision: family today, family still.
        ("BMW", "HA0002N0, HA0003N0", ("HA0002N0", "HA0003N1"), ()),
        ("Audi", "CCWA, CDUD", ("CCWA", "CPNB"), ("CDUD",)),
        # A list unknown to the whole catalog stays unverified.
        ("Tesla", "3D8, 3D1", ("L2S",), ()),
        ("BMW", "W20-K06A, IB1P25B", ("IB1P25B",), ()),
        # A reviewed alias or family never completes a list.
        ("Chevrolet", "A14XER, A14NET", ("LDD", "LUJ"), ("A 14 XER",)),
        ("Subaru", "EJ253, EJ251", ("EJ25",), ("EJ253",)),
    ],
)
def test_a_partial_list_keeps_exactly_the_result_it_had(
    maker: str, car: str, catalog: tuple[str, ...], carriers: tuple[str, ...]
) -> None:
    ktype = _ktype("k", maker, "Model", *catalog, electrification="battery_electric", year_from=2015)
    carrier = _ktype("o", maker, "Other", *carriers) if carriers else None
    others = (carrier,) if carrier else ()
    known: set[str] = set()
    for candidate in (ktype, *others):
        for code in candidate.engine_codes:
            known |= engine_code_forms(code)
            if family := engine_code_family(code, revision=False):
                known.add(family)

    score = _score(car, ktype, *others, year=2022, fuels=frozenset({"electric"}))

    assert _engine_fields(score) == _legacy_engine_fields(car, ktype, known)
    assert not _confirms(score)


def test_a_partial_list_never_downgrades_a_conflict() -> None:
    ev9 = _ktype("k", "Kia", "EV9 (MV)", "EM16", electrification="battery_electric")
    carrier = _ktype("o", "Kia", "EV9 (MV)", "EM18", "EM16", electrification="battery_electric")

    score = _score("EM18, EM16", ev9, carrier, fuels=frozenset({"electric"}))

    assert _engine_fields(score) == {"conflict"}


def test_a_value_that_was_one_exact_code_before_stays_the_exact_engine() -> None:
    # Read as one code, `BHZ, DV6FC` is TecDoc's `BHZ (DV6FC)`: exact before lists were read.
    peugeot = _ktype("k", "Peugeot", "308 II", "BHZ (DV6FC)")

    assert _engine_fields(_score("BHZ, DV6FC", peugeot)) == {"engine_code"}


@pytest.mark.parametrize(
    ("maker", "car", "catalog", "fields"),
    [
        # Alternatives beside a third engine the list does not name.
        ("Dacia", "H5H-470, H5H-480", ("H5H 470", "H5H 480", "H5H 490"), {}),
        ("Porsche", "M64.21, M64.22", ("M 64.21", "M 64.22", "M 64.23", "M 64.24"), {}),
        # An electric KType with a motor the list does not name.
        ("Tesla", "L2S, L2S", ("3D1", "L2S"), {"electrification": "battery_electric"}),
    ],
)
def test_a_ktype_with_engines_beyond_the_list_is_family_evidence_only(
    maker: str, car: str, catalog: tuple[str, ...], fields: dict[str, Any]
) -> None:
    ktype = _ktype("k", maker, "Model", *catalog, year_from=1980, **fields)

    score = _score(car, ktype, year=2020)
    single = _score(catalog[0], ktype, year=2020)

    assert _engine_fields(score) == {"engine_code_family"}
    assert not _confirms(score)
    assert single.separation_score - score.separation_score > _CONFIG.automatic_margin


def test_a_list_does_not_prefer_the_ktype_with_more_engines_over_one_with_part_of_it() -> None:
    # Porsche 993: the Carrera lists four engines, the Carrera 4 two of them.
    carrera = _ktype("c2", "Porsche", "911 (993)", "M 64.21", "M 64.22", "M 64.23", "M 64.24")
    carrera_4 = _ktype("c4", "Porsche", "911 (993)", "M 64.21", "M 64.23")

    whole = _score("M64.21, M64.22", carrera, carrera_4)
    part = _score("M64.21, M64.22", carrera_4, carrera)

    assert _engine_fields(whole) == _engine_fields(part) == {"engine_code_family"}
    assert whole.separation_score == part.separation_score


def test_two_ktypes_holding_exactly_the_list_stay_tied() -> None:
    saloon = _ktype("a", "Dacia", "Duster", "H5H 470", "H5H 480")
    estate = _ktype("b", "Dacia", "Duster", "H5H 470", "H5H 480")
    matcher = FuzzyVehicleMatcher(ManufacturerCandidateIndex((saloon, estate)))

    result = matcher.match(
        VehicleMatchQuery("Duster", manufacturer="Dacia", engine_code="H5H-470, H5H-480")
    )

    assert result.reason == "candidate_margin_not_met"
    assert result.eligible_for_auto_resolution is False


# --- 6. Electric motor codes, scoped to the model family ---------------------------


def _i4_catalog() -> tuple[VehicleCandidate, VehicleCandidate, VehicleCandidate]:
    i4 = _ktype("i4", "BMW", "4 Gran Coupe (G26)", "HA0002N0", "HA0003N0")
    coupe = _ktype("430d", "BMW", "4 Coupe (G22, G82)", "B57 D30 B", "JA1")
    # XE2 is a catalog code, but only on another model family.
    ix = _ktype("ix", "BMW", "iX (I20)", "HA0002N0", "HA0004N0", "XE2")
    return i4, coupe, ix


def test_an_electric_motor_code_from_another_code_system_is_unverified_in_the_model_family() -> None:
    i4, coupe, ix = _i4_catalog()
    electric = frozenset({"electric"})

    score = _score("XE2", i4, coupe, ix, fuels=electric)

    assert _engine_fields(score) == {"engine_code_unverified"}
    assert not score.conflicting_fields
    assert not _confirms(score)
    # The model family is the word its names start with: every `4 ...` KType.
    assert "engine_code_unverified" in _score("XE2", coupe, i4, ix, fuels=electric).missing_fields
    # Where the family carries the code, it is the exact engine as before.
    assert _engine_fields(_score("XE2", ix, i4, coupe, fuels=electric)) == {"engine_code"}


@pytest.mark.parametrize(
    "fuels",
    [frozenset({"petrol"}), frozenset({"electric", "petrol"}), frozenset({"electric", "hybrid petrol"}), frozenset()],
)
def test_the_model_family_scope_is_for_fully_electric_cars_only(fuels: frozenset[str]) -> None:
    i4, coupe, ix = _i4_catalog()

    assert "engine_code" in _score("XE2", i4, coupe, ix, fuels=fuels).conflicting_fields


@pytest.mark.parametrize(
    ("maker", "car", "model", "catalog", "sibling_model", "sibling"),
    [
        # ID.3: registry and TecDoc speak one code system, so another code contradicts.
        ("Volkswagen", "EDCC", "ID.3 (E11, E12)", "EDCA", "ID.3 (E11, E12)", "EDCC"),
        # ID.4 `EEWA` sits on an ID.3 KType: the same `ID` model family.
        ("Volkswagen", "EEWA", "ID.4 (E21)", "EDDA", "ID.3 (E11, E12)", "EEWA"),
        # Zoe `5AQ-80` is Renault's truncated index: its first-token family is in the family.
        ("Renault", "5AQ-80", "ZOE (BFM_)", "5AQ 605", "ZOE (BFM_)", "5AQ 607"),
        ("Tesla", "3D8", "MODEL S", "L2S", "MODEL S", "3D8"),
    ],
)
def test_an_electric_code_the_model_family_carries_still_conflicts(
    maker: str, car: str, model: str, catalog: str, sibling_model: str, sibling: str
) -> None:
    ktype = _ktype("k", maker, model, catalog)
    other = _ktype("o", maker, sibling_model, sibling)

    score = _score(car, ktype, other, fuels=frozenset({"electric"}))

    assert _engine_fields(score) == {"conflict"}


def test_an_electric_code_no_ktype_carries_is_unverified_as_before() -> None:
    model_y = _ktype("k", "Tesla", "MODEL Y", "3D3", "4D1")

    score = _score("3DU", model_y, fuels=frozenset({"electric"}))

    assert _engine_fields(score) == {"engine_code_unverified"}


# --- Knowledge of an engine ---------------------------------------------------------


def test_knows_engine_reads_reviewed_spellings_in_the_makers_scope() -> None:
    index = ManufacturerCandidateIndex(
        (
            _ktype("mb", "MERCEDES-BENZ", "C-CLASS (W206)", "139.580"),
            _ktype("bmw", "BMW", "3 (E46)", "M52 B20 (206S4)"),
            _ktype("saab", "Saab", "9-5 (YS3E)", "B205E"),
        )
    )

    assert index.knows_engine("M139.580", "MERCEDES-BENZ")
    assert index.knows_engine("B205E/B", "Saab")
    assert index.knows_engine("M52-TUB20", "BMW")
    # Without the maker the written form decides, as before.
    assert not index.knows_engine("M139.580")
    assert not index.knows_engine("B205E/B")
    assert index.knows_engine("M52-TUB20")  # through its first-token family M52
    assert not index.knows_engine("EJ254", "Subaru")
