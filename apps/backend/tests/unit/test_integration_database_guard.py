from tests.integration.database_guard import integration_skip_reason


def test_runs_integration_tests_in_the_test_environment() -> None:
    assert integration_skip_reason("test", read_only=False) is None


def test_skips_writing_integration_tests_outside_the_test_environment() -> None:
    for environment in ("local", "staging", "production"):
        reason = integration_skip_reason(environment, read_only=False)
        assert reason is not None
        assert "ENVIRONMENT=test" in reason


def test_read_only_tests_run_against_any_database() -> None:
    assert integration_skip_reason("local", read_only=True) is None
