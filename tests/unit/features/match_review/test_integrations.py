import pytest

from api.app.features.match_review.integrations import (
    OemProviderNotConfiguredError,
    UnconfiguredOemVinProvider,
    mask_vin,
)


def test_mask_vin_keeps_only_edges() -> None:
    assert mask_vin("YV1SW6151E1234567") == "YV1***********567"
    assert mask_vin(" YV1SW6151E1234567 ") == "YV1***********567"


def test_mask_vin_hides_short_values_entirely() -> None:
    assert mask_vin("ABC123") == "******"
    assert mask_vin("AB") == "**"


def test_unconfigured_provider_refuses_lookups() -> None:
    provider = UnconfiguredOemVinProvider()

    with pytest.raises(OemProviderNotConfiguredError):
        provider.fetch_vehicle("YV1SW6151E1234567")
