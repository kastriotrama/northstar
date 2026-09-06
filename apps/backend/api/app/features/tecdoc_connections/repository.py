import json
from pathlib import Path
from typing import Any


class ResolvedConnectionRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> tuple[dict[str, Any], ...]:
        payload = json.loads(self._path.read_text())
        items = payload.get("items")
        if not isinstance(items, list):
            raise TypeError("resolved showcase items are missing")
        return tuple(dict(item) for item in items if isinstance(item, dict))
