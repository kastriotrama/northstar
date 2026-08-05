"""Create the reviewed SCRUM-94 corpus inputs before explicit golden approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ingestion.golden_corpus import GOLDEN_CORPUS_VERSION, approve_corpus

CORPUS_PATH = Path("tests/golden/normalization-reconciliation-v1.json")

MANUFACTURERS = (
    "Volvo Car Corporation",
    "Mercedes-Benz AG",
    "BMW AG",
    "Audi AG",
    "Volkswagen AG",
    "Iveco SPA",
    "Scania CV AB",
    "Ford Motor Company",
    "Toyota Motor Corporation",
    "Renault SAS",
    "Peugeot",
    "Citroën",
    "Fiat Auto SPA",
    "Opel Automobile GmbH",
    "Nissan Motor Co Ltd",
    "Honda Motor Co Ltd",
    "MAN Truck Bus",
    "DAF Trucks NV",
    "Lexus",
    "Porsche AG",
)


def _normalization_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, manufacturer in enumerate(MANUFACTURERS, start=1):
        model = f"Model {index:02d}"
        scenarios = (
            (
                "common-passenger",
                "Common passenger registration with reviewed bodywork and transmission codes",
                {"model": model, "eu_category": "M1", "body_code": "AC", "gearbox": "Z"},
                ["common", "bodywork", "transmission"],
            ),
            (
                "dates",
                "Registration and production dates with explicit precision",
                {
                    "model": model,
                    "registration_date": f"2024{((index - 1) % 12) + 1:02d}15",
                    "build_month": f"2023{((index + 4) % 12) + 1:02d}",
                },
                ["dates", "common"],
            ),
            (
                "engine",
                "Structured engine identifiers and metric measurements",
                {
                    "model": model,
                    "engine_code": f" eng{index:02d} ",
                    "engine_family_code": f" fam{index:02d} ",
                    "engine_family_name": f"Engine Family {index:02d}",
                    "kw": 80 + index,
                    "ccm": 1200 + (index * 37),
                },
                ["engine", "measurements"],
            ),
            (
                "electrification",
                "Plug-in hybrid retaining both underlying energy sources",
                {
                    "model": model,
                    "fuel1": "01",
                    "fuel2": "03",
                    "ev_config": "Laddhybrid",
                },
                ["fuel", "electrification", "hybrid"],
            ),
            (
                "dual-fuel",
                "Bi-fuel vehicle retaining petrol and natural-gas evidence",
                {"model": model, "fuel1": "01", "fuel2": "09", "fuel_combo": "B"},
                ["fuel", "dual-fuel", "rare"],
            ),
            (
                "drive-marketing",
                "Manufacturer-scoped all-wheel-drive marketing candidate",
                {"model": f"{model} quattro", "is_4wd": "1"},
                ["drive", "marketing", "provisional"],
            ),
            (
                "malformed",
                "Malformed dates, transmission and fuel codes routed to review",
                {"model": model, "build_date": "20241341", "gearbox": "?", "fuel1": "99"},
                ["ambiguous", "invalid", "review"],
            ),
            (
                "unknown-manufacturer",
                "Unknown converter cannot silently inherit a populated base manufacturer",
                {
                    "manufacturer": f"Unclassified Converter {index:02d}",
                    "base_manufacturer": manufacturer,
                    "model": model,
                },
                ["manufacturer", "ambiguous", "review"],
            ),
        )
        for scenario, description, fields, tags in scenarios:
            raw_record = {"manufacturer": manufacturer, **fields}
            if scenario == "unknown-manufacturer":
                raw_record = fields
            cases.append(
                {
                    "id": f"norm-{index:02d}-{scenario}",
                    "description": description,
                    "tags": tags,
                    "input": {"kind": "normalization", "raw_record": raw_record},
                    "expected": {},
                }
            )
    return cases


def _catalog(reference: str, manufacturer: str, model: str) -> dict[str, Any]:
    return {
        "candidate_reference": reference,
        "candidate_type": "TecDocKType",
        "manufacturer": manufacturer,
        "model": model,
        "year_from": 2018,
        "year_to": 2025,
        "fuels": ["petrol"],
        "engine_codes": ["ENG1"],
    }


def _reconciliation_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    manufacturers = (
        "Volvo",
        "BMW",
        "Audi",
        "Volkswagen",
        "Toyota",
        "Ford",
        "Honda",
        "Nissan",
        "Renault",
        "Porsche",
    )
    for index, manufacturer in enumerate(manufacturers, start=1):
        reference = f"KTYPE-GOLD-{index:02d}"
        model = f"ZX{index}00"
        base = _catalog(reference, manufacturer, model)
        scenarios = (
            (
                "exact",
                "Exact manufacturer, model, year, fuel and engine evidence",
                [base],
                {
                    "manufacturer": manufacturer,
                    "model": model,
                    "year": 2022,
                    "fuels": ["petrol"],
                    "engine_code": "ENG1",
                },
                ["reconciliation", "resolved", "common"],
            ),
            (
                "inexact",
                "Inexact model with sufficient evidence remains provisional",
                [base],
                {"manufacturer": manufacturer, "model": model.replace("X", "")},
                ["reconciliation", "provisional", "fuzzy"],
            ),
            (
                "fuel-conflict",
                "Fuel conflict blocks automatic reconciliation",
                [base],
                {
                    "manufacturer": manufacturer,
                    "model": model,
                    "year": 2022,
                    "fuels": ["diesel"],
                    "engine_code": "ENG1",
                },
                ["reconciliation", "conflict", "review"],
            ),
            (
                "ambiguous",
                "Equal candidate scores require review with alternatives",
                [
                    base,
                    _catalog(f"{reference}-ALT", manufacturer, model),
                ],
                {"manufacturer": manufacturer, "model": model},
                ["reconciliation", "ambiguous", "review"],
            ),
        )
        for scenario, description, catalog, query, tags in scenarios:
            cases.append(
                {
                    "id": f"match-{index:02d}-{scenario}",
                    "description": description,
                    "tags": tags,
                    "input": {"kind": "reconciliation", "catalog": catalog, "query": query},
                    "expected": {},
                }
            )
    return cases


def _reviewed_bodywork_cases() -> list[dict[str, Any]]:
    examples = (
        (
            "bodywork-official-af",
            "Official AF remains the broader multi-purpose vehicle form",
            {"manufacturer": "Volkswagen", "eu_category": "M1", "body_code": "AF"},
            ["bodywork", "official", "multi-purpose"],
        ),
        (
            "bodywork-official-ba",
            "Official goods category BA uses the reviewed truck form",
            {"manufacturer": "Volvo", "eu_category": "N1", "body_code": "BA"},
            ["bodywork", "official", "truck"],
        ),
        (
            "bodywork-goods-van",
            "Goods-category panel and cargo marketing terms remain van",
            {"manufacturer": "Ford", "eu_category": "N1", "model": "Transit Cargo Van"},
            ["bodywork", "marketing", "van"],
        ),
        (
            "bodywork-passenger-van",
            "Passenger-category marketing term uses passenger_van",
            {"manufacturer": "Volkswagen", "eu_category": "M1", "model": "Multivan"},
            ["bodywork", "marketing", "passenger-van"],
        ),
        (
            "bodywork-af-passenger-compatible",
            "Official AF wins without conflict when passenger-van marketing evidence agrees",
            {
                "manufacturer": "Volkswagen",
                "eu_category": "M1",
                "body_code": "AF",
                "model": "Multivan",
            },
            ["bodywork", "official", "marketing", "compatibility"],
        ),
    )
    return [
        {
            "id": case_id,
            "description": description,
            "tags": tags,
            "input": {"kind": "normalization", "raw_record": raw_record},
            "expected": {},
        }
        for case_id, description, raw_record, tags in examples
    ]


def main() -> None:
    cases = [*_normalization_cases(), *_reviewed_bodywork_cases(), *_reconciliation_cases()]
    if len(cases) != 205:
        raise RuntimeError(f"expected exactly 205 curated cases, got {len(cases)}")
    CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_text(
        json.dumps(
            {
                "corpus_version": GOLDEN_CORPUS_VERSION,
                "description": (
                    "Sanitized common, rare and ambiguous normalization and reconciliation cases."
                ),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report = approve_corpus(CORPUS_PATH)
    print(f"generated and approved {report.case_count} cases at {CORPUS_PATH}")


if __name__ == "__main__":
    main()
