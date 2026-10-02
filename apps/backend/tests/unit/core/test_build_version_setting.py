import pytest

from api.app.core.settings import Settings


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_build_version_is_unknown(value: str) -> None:
    assert Settings(BUILD_VERSION=value, _env_file=None).build_version == "unknown"  # type: ignore[call-arg]


def test_the_build_version_is_the_deploys_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_VERSION", " 18dee52 ")
    assert Settings(_env_file=None).build_version == "18dee52"  # type: ignore[call-arg]
    monkeypatch.delenv("BUILD_VERSION")
    assert Settings(_env_file=None).build_version == "unknown"  # type: ignore[call-arg]
