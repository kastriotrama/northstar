from api.app.core.health import StoreHealth, run_health_checks


class FakeClient:
    def __init__(self, name: str, result: StoreHealth | None = None) -> None:
        self.name = name
        self._result = result

    def check(self) -> StoreHealth:
        if self._result is None:
            raise RuntimeError("store unavailable")
        return self._result


def test_run_health_checks_returns_result_per_client_in_order() -> None:
    clients = [
        FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
        FakeClient("redis", StoreHealth(name="redis", status="ok")),
    ]

    results = run_health_checks(clients)

    assert results == [
        StoreHealth(name="postgres", status="ok"),
        StoreHealth(name="redis", status="ok"),
    ]


def test_run_health_checks_converts_raised_exceptions_to_error_results() -> None:
    clients = [
        FakeClient("postgres", StoreHealth(name="postgres", status="ok")),
        FakeClient("neo4j"),
    ]

    results = run_health_checks(clients)

    assert results == [
        StoreHealth(name="postgres", status="ok"),
        StoreHealth(name="neo4j", status="error", detail="RuntimeError"),
    ]


def test_run_health_checks_with_no_clients_returns_empty_list() -> None:
    assert run_health_checks([]) == []
