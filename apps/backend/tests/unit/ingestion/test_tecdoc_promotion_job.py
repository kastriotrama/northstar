from typing import Any, cast

from neo4j import Driver

from ingestion.tecdoc.promotion_job import promote_graph_in_chunks


def test_graph_promotion_is_chunked_and_reconciled() -> None:
    calls: list[int] = []

    def writer(_driver: Driver, rows: tuple[Any, ...]) -> int:
        calls.append(len(rows))
        return len(rows)

    written, chunks = promote_graph_in_chunks(
        cast(Driver, object()), cast(Any, tuple(range(1201))), chunk_size=500, writer=writer
    )
    assert (written, chunks) == (1201, 3)
    assert calls == [500, 500, 201]


def test_graph_promotion_rejects_invalid_chunk_size() -> None:
    try:
        promote_graph_in_chunks(cast(Driver, object()), (), chunk_size=0)
    except ValueError as error:
        assert str(error) == "chunk_size must be positive"
    else:
        raise AssertionError("Expected invalid chunk size to fail")
