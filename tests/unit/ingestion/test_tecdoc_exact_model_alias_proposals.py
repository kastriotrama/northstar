from ingestion.tecdoc.exact_model_alias_proposals import (
    ExactModelAliasObservation,
    propose_exact_model_aliases,
)


def test_proposes_repeated_single_target_catalog_alias() -> None:
    rows = (
        ExactModelAliasObservation("Volvo", "XC FOURTY", "XC40"),
        ExactModelAliasObservation("Volvo", "XC FOURTY", "XC40"),
        ExactModelAliasObservation("Volvo", "XC FOURTY", None, "phonetic_only"),
        ExactModelAliasObservation("Volvo", "XC FOURTY", None, "phonetic_only"),
    )

    proposals = propose_exact_model_aliases(
        rows,
        allowed_models_by_manufacturer={"Volvo": ("XC40",)},
    )

    assert len(proposals) == 1
    assert proposals[0].source_term == "XC FOURTY"
    assert proposals[0].canonical_model == "XC40"
    assert proposals[0].anchor_count == 2
    assert proposals[0].unresolved_count == 2


def test_rejects_conflicting_target_or_alias_equal_to_canonical() -> None:
    conflicting = (
        ExactModelAliasObservation("Volvo", "X C", "XC40"),
        ExactModelAliasObservation("Volvo", "X C", "XC60"),
        ExactModelAliasObservation("Volvo", "X C", None, "no_candidate"),
        ExactModelAliasObservation("Volvo", "X C", None, "no_candidate"),
    )
    equal = (
        ExactModelAliasObservation("Volvo", "XC40", "XC40"),
        ExactModelAliasObservation("Volvo", "XC40", "XC40"),
        ExactModelAliasObservation("Volvo", "XC40", None, "phonetic_only"),
        ExactModelAliasObservation("Volvo", "XC40", None, "phonetic_only"),
    )
    allowed = {"Volvo": ("XC40", "XC60")}

    assert propose_exact_model_aliases(
        conflicting, allowed_models_by_manufacturer=allowed
    ) == ()
    assert propose_exact_model_aliases(equal, allowed_models_by_manufacturer=allowed) == ()


def test_rejects_numeric_or_insufficient_evidence() -> None:
    numeric = (
        ExactModelAliasObservation("Polestar", "2", "POLESTAR2"),
        ExactModelAliasObservation("Polestar", "2", "POLESTAR2"),
        ExactModelAliasObservation("Polestar", "2", None, "phonetic_only"),
        ExactModelAliasObservation("Polestar", "2", None, "phonetic_only"),
    )

    assert propose_exact_model_aliases(
        numeric,
        allowed_models_by_manufacturer={"Polestar": ("POLESTAR2",)},
    ) == ()


def test_preserves_catalog_model_display_value() -> None:
    rows = (
        ExactModelAliasObservation("Mercedes-Benz", "C 220 D KOMBI", "C220 CDI Estate"),
        ExactModelAliasObservation("Mercedes-Benz", "C 220 D KOMBI", "C220 CDI Estate"),
        ExactModelAliasObservation(
            "Mercedes-Benz", "C 220 D KOMBI", None, "no_candidate"
        ),
        ExactModelAliasObservation(
            "Mercedes-Benz", "C 220 D KOMBI", None, "no_candidate"
        ),
    )

    proposals = propose_exact_model_aliases(
        rows,
        allowed_models_by_manufacturer={"Mercedes-Benz": ("C220 CDI Estate",)},
    )

    assert proposals[0].canonical_model == "C220 CDI Estate"
