from pathlib import Path

import pytest

from ingestion.config import get_ingestion_settings
from tests.integration.database_guard import READ_ONLY_MARKER, integration_skip_reason

_INTEGRATION_DIR = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    environment = get_ingestion_settings().environment
    for item in items:
        if not item.path.is_relative_to(_INTEGRATION_DIR):
            continue
        read_only = item.get_closest_marker(READ_ONLY_MARKER) is not None
        reason = integration_skip_reason(environment, read_only=read_only)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))
