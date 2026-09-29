"""Keep integration tests off databases that hold real data.

Most integration tests write migrations and test batches straight into the
database `DATABASE_URL` points at. Locally that is usually a full copy of live,
where a leftover test catalog batch once made every car match "none". They run
only when `ENVIRONMENT=test` says the datastores are disposable (as in CI).
"""

from __future__ import annotations

READ_ONLY_MARKER = "read_only_database"


def integration_skip_reason(environment: str, *, read_only: bool) -> str | None:
    if environment == "test" or read_only:
        return None
    return (
        f"ENVIRONMENT={environment!r}: integration tests write to DATABASE_URL and run only "
        "with ENVIRONMENT=test against disposable datastores"
    )
