"""Registry tyre and type-approval notations the parsers accept and reject."""

from __future__ import annotations

from typing import Any

import pytest

from ingestion.normalization_rules import _TYPE_APPROVAL, _parse_tyre


def _approval(text: str) -> dict[str, str | None] | None:
    match = _TYPE_APPROVAL.match(text)
    return None if match is None else match.groupdict()


@pytest.mark.parametrize(
    ("text", "directive", "number", "extension"),
    [
        ("e1*2007/46*0480*12", "2007/46", "0480", "12"),
        ("e1*2007/46*1320", "2007/46", "1320", None),
        ("e11*KS07/46*0040*03", "KS07/46", "0040", "03"),
        ("e4*KS18/858*00009*02", "KS18/858", "00009", "02"),
        ("e1*2001/116*0144*", "2001/116", "0144", ""),
        ("e13*2001/116*0089* ", "2001/116", "0089", ""),
    ],
)
def test_type_approval_formats_are_recognized(
    text: str, directive: str, number: str, extension: str | None
) -> None:
    parts = _approval(text)

    assert parts is not None
    assert (parts["directive"], parts["number"], parts["extension"]) == (
        directive,
        number,
        extension,
    )


@pytest.mark.parametrize(
    "text",
    [
        "-",
        "e*13*97/27*0037*02",
        "e1*2001/116/0317*01",
        "e1/2001/116/0414*00",
        "e1*2007/46*0421-15",
        "e1*98/14D0080*07",
        "e1*98/14PD0193*02",
        "e13*2007/461185*03",
        "e2*2001/116/*0328",
        "e11*2001/116Ü*0199*01",
        "e11*KS*0040*03",
        "e5*NKS*9993*00",
        "e5*NKS07/46*9993*00",
        "e1*2001/116*0144**",
    ],
)
def test_type_approval_typos_stay_in_review(text: str) -> None:
    assert _approval(text) is None


def _spec(text: str) -> dict[str, Any]:
    spec = _parse_tyre(text)
    assert spec is not None, text
    assert spec.pop("raw") == text.strip()
    return spec


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "185/70SR14",
            {
                "size_system": "metric",
                "section_width_mm": 185,
                "aspect_ratio": 70,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "speed_symbol": "S",
            },
        ),
        (
            "P215/70SR14 96S",
            {
                "size_system": "p_metric",
                "section_width_mm": 215,
                "aspect_ratio": 70,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "load_index": 96,
                "speed_symbol": "S",
            },
        ),
        (
            "195/70VR14",
            {
                "size_system": "metric",
                "section_width_mm": 195,
                "aspect_ratio": 70,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "speed_symbol": "V",
            },
        ),
        (
            "225/50RF18",
            {
                "size_system": "metric",
                "section_width_mm": 225,
                "aspect_ratio": 50,
                "construction": "radial",
                "run_flat": True,
                "rim_diameter_in": 18.0,
            },
        ),
        (
            "265/30 ZRF21 99Y",
            {
                "size_system": "metric",
                "section_width_mm": 265,
                "aspect_ratio": 30,
                "construction": "radial",
                "run_flat": True,
                "rim_diameter_in": 21.0,
                "load_index": 99,
                "speed_symbol": "Y",
            },
        ),
        (
            "215/40Z R17 83W",
            {
                "size_system": "metric",
                "section_width_mm": 215,
                "aspect_ratio": 40,
                "construction": "radial",
                "rim_diameter_in": 17.0,
                "load_index": 83,
                "speed_symbol": "W",
            },
        ),
        (
            "245/45/R19 102Y",
            {
                "size_system": "metric",
                "section_width_mm": 245,
                "aspect_ratio": 45,
                "construction": "radial",
                "rim_diameter_in": 19.0,
                "load_index": 102,
                "speed_symbol": "Y",
            },
        ),
        (
            "245/45/R19, 102Y",
            {
                "size_system": "metric",
                "section_width_mm": 245,
                "aspect_ratio": 45,
                "construction": "radial",
                "rim_diameter_in": 19.0,
                "load_index": 102,
                "speed_symbol": "Y",
            },
        ),
        (
            "255/45/R20-105Y",
            {
                "size_system": "metric",
                "section_width_mm": 255,
                "aspect_ratio": 45,
                "construction": "radial",
                "rim_diameter_in": 20.0,
                "load_index": 105,
                "speed_symbol": "Y",
            },
        ),
        (
            "HL235/45 R21 104T XL",
            {
                "size_system": "metric",
                "section_width_mm": 235,
                "aspect_ratio": 45,
                "construction": "radial",
                "rim_diameter_in": 21.0,
                "load_index": 104,
                "speed_symbol": "T",
                "load_range": "hl",
            },
        ),
        (
            "HL 295/30 ZR21105Y",
            {
                "size_system": "metric",
                "section_width_mm": 295,
                "aspect_ratio": 30,
                "construction": "radial",
                "rim_diameter_in": 21.0,
                "load_index": 105,
                "speed_symbol": "Y",
                "load_range": "hl",
            },
        ),
        (
            "175R1488S",
            {
                "size_system": "alpha",
                "section_width_mm": 175,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "load_index": 88,
                "speed_symbol": "S",
            },
        ),
        (
            "155   R13 77R",
            {
                "size_system": "alpha",
                "section_width_mm": 155,
                "construction": "radial",
                "rim_diameter_in": 13.0,
                "load_index": 77,
                "speed_symbol": "R",
            },
        ),
        (
            "185R14C",
            {
                "size_system": "alpha",
                "section_width_mm": 185,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "load_range": "c",
            },
        ),
        (
            "195 R14C 102",
            {
                "size_system": "alpha",
                "section_width_mm": 195,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "load_range": "c",
                "load_index": 102,
            },
        ),
        (
            "185R14C/6",
            {
                "size_system": "alpha",
                "section_width_mm": 185,
                "construction": "radial",
                "rim_diameter_in": 14.0,
                "load_range": "c",
                "ply_rating": 6,
            },
        ),
        (
            "165HR15 89H",
            {
                "size_system": "alpha",
                "section_width_mm": 165,
                "construction": "radial",
                "rim_diameter_in": 15.0,
                "load_index": 89,
                "speed_symbol": "H",
            },
        ),
        (
            "165SR15/REINFOR",
            {
                "size_system": "alpha",
                "section_width_mm": 165,
                "construction": "radial",
                "rim_diameter_in": 15.0,
                "speed_symbol": "S",
                "load_range": "xl",
            },
        ),
        (
            "H78-15",
            {
                "size_system": "alphanumeric",
                "load_letter": "H",
                "aspect_ratio": 78,
                "construction": "bias",
                "rim_diameter_in": 15.0,
            },
        ),
        (
            "GR70-15",
            {
                "size_system": "alphanumeric",
                "load_letter": "G",
                "aspect_ratio": 70,
                "construction": "radial",
                "rim_diameter_in": 15.0,
            },
        ),
        (
            "L 78-15",
            {
                "size_system": "alphanumeric",
                "load_letter": "L",
                "aspect_ratio": 78,
                "construction": "bias",
                "rim_diameter_in": 15.0,
            },
        ),
        (
            "215/650R44096Y",
            {
                "size_system": "pax",
                "section_width_mm": 215,
                "overall_diameter_mm": 650,
                "construction": "radial",
                "rim_diameter_mm": 440,
                "run_flat": True,
                "load_index": 96,
                "speed_symbol": "Y",
            },
        ),
        (
            "215/650R440A96Y",
            {
                "size_system": "pax",
                "section_width_mm": 215,
                "overall_diameter_mm": 650,
                "construction": "radial",
                "rim_diameter_mm": 440,
                "run_flat": True,
                "load_index": 96,
                "speed_symbol": "Y",
            },
        ),
        (
            "165R400",
            {
                "size_system": "metric",
                "section_width_mm": 165,
                "construction": "radial",
                "rim_diameter_mm": 400,
            },
        ),
        (
            "125-380",
            {
                "size_system": "metric",
                "section_width_mm": 125,
                "construction": "bias",
                "rim_diameter_mm": 380,
            },
        ),
        (
            "5.60-15/4",
            {
                "size_system": "imperial",
                "section_width_in": 5.6,
                "construction": "bias",
                "rim_diameter_in": 15.0,
                "ply_rating": 4,
            },
        ),
        (
            "5.20S-10",
            {
                "size_system": "imperial",
                "section_width_in": 5.2,
                "construction": "bias",
                "rim_diameter_in": 10.0,
                "speed_symbol": "S",
            },
        ),
        (
            "9.00-16C",
            {
                "size_system": "imperial",
                "section_width_in": 9.0,
                "construction": "bias",
                "rim_diameter_in": 16.0,
                "load_range": "c",
            },
        ),
        (
            "7.25R13",
            {
                "size_system": "imperial",
                "section_width_in": 7.25,
                "construction": "radial",
                "rim_diameter_in": 13.0,
            },
        ),
        (
            "6.40SR13",
            {
                "size_system": "imperial",
                "section_width_in": 6.4,
                "construction": "radial",
                "rim_diameter_in": 13.0,
                "speed_symbol": "S",
            },
        ),
        (
            "8.20 R15 102S",
            {
                "size_system": "imperial",
                "section_width_in": 8.2,
                "construction": "radial",
                "rim_diameter_in": 15.0,
                "load_index": 102,
                "speed_symbol": "S",
            },
        ),
        (
            "10,00R17,5",
            {
                "size_system": "imperial",
                "section_width_in": 10.0,
                "construction": "radial",
                "rim_diameter_in": 17.5,
            },
        ),
        (
            "31X10,50R15 LT",
            {
                "size_system": "flotation",
                "overall_diameter_in": 31.0,
                "section_width_in": 10.5,
                "construction": "radial",
                "rim_diameter_in": 15.0,
                "load_range": "lt",
            },
        ),
    ],
)
def test_real_tyre_notations_are_parsed(text: str, expected: dict[str, Any]) -> None:
    assert _spec(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "225/50 R17 98V",
            {
                "size_system": "metric",
                "section_width_mm": 225,
                "aspect_ratio": 50,
                "construction": "radial",
                "rim_diameter_in": 17.0,
                "load_index": 98,
                "speed_symbol": "V",
            },
        ),
        (
            "265/35 ZR21101YXL",
            {
                "size_system": "metric",
                "section_width_mm": 265,
                "aspect_ratio": 35,
                "construction": "radial",
                "rim_diameter_in": 21.0,
                "load_index": 101,
                "speed_symbol": "YX",
            },
        ),
        (
            "165SR15",
            {
                "size_system": "alpha",
                "section_width_mm": 165,
                "construction": "radial",
                "rim_diameter_in": 15.0,
                "speed_symbol": "S",
            },
        ),
        (
            "5,00-18",
            {
                "size_system": "imperial",
                "section_width_in": 5.0,
                "construction": "bias",
                "rim_diameter_in": 18.0,
            },
        ),
    ],
)
def test_notations_parsed_before_keep_their_result(text: str, expected: dict[str, Any]) -> None:
    assert _spec(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # no usable size: axle labels, rim specifications, missing rim or width
        "AXLE1   22",
        "22",
        "15X6.0",
        "15X5J",
        "33X4",
        "165-7.0",
        "195/60",
        "245/35R",
        "335/ZR20",
        "70-14",
        "5/65R17 17",
        "75/65R14 82/86T",
        "15/70R16 100H",
        "95/65R15LRR 91H",
        # wrong separators and stray letters are typos, not notations
        "215*55R17 94V",
        "235&60R16 100H",
        "345//30R19",
        "205/55rR16",
        "R225/60R15",
        "245/40Z18",
        "195/60/15",
        "165SR15/R",
        # two widths, a rim-like "X" size, unknown suffixes and rims
        "4,40/4,50-21",
        "5.00X19",
        "7.00X16",
        "29.04-14",
        "6,00-15L",
        "28,0/10,5R15",
        "135/380",
        "165R123",
        "ER70V15",
        "P78-15",
        "G65-14",
        "215/650R44096Y*",
        "185R14C/5",
    ],
)
def test_text_without_an_unambiguous_tyre_size_stays_unrecognized(text: str) -> None:
    assert _parse_tyre(text) is None
