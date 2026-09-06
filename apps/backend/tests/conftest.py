from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from api.app.main import create_app
from ingestion.normalization_rules import PIPELINE_VERSION


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


@pytest.fixture
def current_normalization_bundle(tmp_path: Path) -> Path:
    """Keep the historic fixture intact; retag only its pipeline metadata.

    The fixture contains no fuel carriers. Its actual normalization content is
    unchanged by v6; the integration test still verifies every stored value.
    """
    source = Path(__file__).parent / "fixtures" / "normalization_bundle_minimal.xlsx"
    target = tmp_path / "current-normalization-bundle.xlsx"
    with ZipFile(source) as original, ZipFile(target, "w") as updated:
        for entry in original.infolist():
            content = original.read(entry.filename)
            if entry.filename.endswith(".xml"):
                content = content.replace(b"normalization-pipeline-v5", PIPELINE_VERSION.encode())
            updated.writestr(entry, content)
    return target
