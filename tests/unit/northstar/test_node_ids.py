from collections.abc import Callable

import pytest

from northstar.node_ids import (
    CROCKFORD_ALPHABET,
    MAX_ULID_TIMESTAMP_MS,
    InvalidNodeIdError,
    NodeIdGenerator,
    NodeIdPrefix,
    is_valid_node_id,
    mint_node_id,
    parse_node_id,
)

FIXED_TIMESTAMP_MS = 1_469_918_176_385
KNOWN_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
KNOWN_ULID_TIMESTAMP_MS = 1_469_922_850_259


def fixed_clock(timestamp_ms: int = FIXED_TIMESTAMP_MS) -> Callable[[], int]:
    return lambda: timestamp_ms


def fixed_entropy(value: bytes = bytes(10)) -> Callable[[int], bytes]:
    def provide(size: int) -> bytes:
        assert size == 10
        return value

    return provide


class IncrementingEntropy:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self, size: int) -> bytes:
        assert size == 10
        current = self.value
        self.value += 1
        return current.to_bytes(size, byteorder="big")


@pytest.mark.parametrize("prefix", list(NodeIdPrefix))
def test_generator_mints_every_canonical_prefix(prefix: NodeIdPrefix) -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=fixed_entropy())

    node_id = generator.mint(prefix)

    assert len(node_id) == 30
    assert node_id.startswith(f"{prefix.value}-")
    assert set(node_id[4:]) <= set(CROCKFORD_ALPHABET)
    assert parse_node_id(node_id).prefix is prefix


def test_parse_known_ulid_returns_timestamp_and_payload() -> None:
    parsed = parse_node_id(f"ENG-{KNOWN_ULID}")

    assert parsed.prefix is NodeIdPrefix.ENGINE
    assert parsed.ulid == KNOWN_ULID
    assert parsed.timestamp_ms == KNOWN_ULID_TIMESTAMP_MS


def test_parse_round_trip_uses_injected_timestamp() -> None:
    timestamp_ms = 1_720_000_000_123
    generator = NodeIdGenerator(
        clock_ms=fixed_clock(timestamp_ms),
        entropy=fixed_entropy(bytes.fromhex("0123456789abcdef0123")),
    )

    parsed = parse_node_id(generator.mint(NodeIdPrefix.VEHICLE_VARIANT))

    assert parsed.prefix is NodeIdPrefix.VEHICLE_VARIANT
    assert parsed.timestamp_ms == timestamp_ms


def test_zero_timestamp_and_entropy_encode_as_zero_ulid() -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(0), entropy=fixed_entropy())

    node_id = generator.mint(NodeIdPrefix.MANUFACTURER)

    assert node_id == f"MFR-{'0' * 26}"


def test_maximum_timestamp_and_entropy_fit_canonical_ulid() -> None:
    generator = NodeIdGenerator(
        clock_ms=fixed_clock(MAX_ULID_TIMESTAMP_MS),
        entropy=fixed_entropy(bytes.fromhex("ff" * 10)),
    )

    node_id = generator.mint(NodeIdPrefix.TRANSMISSION)

    assert node_id == f"TRN-7{'Z' * 25}"
    assert parse_node_id(node_id).timestamp_ms == MAX_ULID_TIMESTAMP_MS


def test_ids_sort_by_distinct_millisecond_timestamp() -> None:
    earlier = NodeIdGenerator(
        clock_ms=fixed_clock(1_000), entropy=fixed_entropy(bytes.fromhex("ff" * 10))
    ).mint(NodeIdPrefix.ALIAS)
    later = NodeIdGenerator(clock_ms=fixed_clock(1_001), entropy=fixed_entropy()).mint(
        NodeIdPrefix.ALIAS
    )

    assert earlier < later


def test_generator_produces_unique_ids_within_one_millisecond() -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=IncrementingEntropy())

    generated = {generator.mint(NodeIdPrefix.PLATFORM) for _ in range(10_000)}

    assert len(generated) == 10_000


def test_mint_accepts_canonical_prefix_string() -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=fixed_entropy())

    assert generator.mint("MFR").startswith("MFR-")


@pytest.mark.parametrize("prefix", ["BAD", "eng", "", "ENGINE"])
def test_mint_rejects_unknown_prefix(prefix: str) -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=fixed_entropy())

    with pytest.raises(ValueError, match="Unsupported node ID prefix"):
        generator.mint(prefix)


def test_mint_rejects_non_string_prefix() -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=fixed_entropy())

    with pytest.raises(TypeError, match="prefix"):
        generator.mint(123)  # type: ignore[arg-type]


@pytest.mark.parametrize("timestamp_ms", [-1, MAX_ULID_TIMESTAMP_MS + 1])
def test_mint_rejects_out_of_range_timestamp(timestamp_ms: int) -> None:
    generator = NodeIdGenerator(
        clock_ms=fixed_clock(timestamp_ms), entropy=fixed_entropy()
    )

    with pytest.raises(ValueError, match="timestamp"):
        generator.mint(NodeIdPrefix.ENGINE)


def test_mint_rejects_boolean_timestamp() -> None:
    generator = NodeIdGenerator(clock_ms=lambda: True, entropy=fixed_entropy())

    with pytest.raises(TypeError, match="timestamp"):
        generator.mint(NodeIdPrefix.ENGINE)


@pytest.mark.parametrize("entropy", [b"short", bytes(11)])
def test_mint_rejects_wrong_entropy_length(entropy: bytes) -> None:
    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=fixed_entropy(entropy))

    with pytest.raises(ValueError, match="exactly 10 bytes"):
        generator.mint(NodeIdPrefix.ENGINE)


def test_mint_rejects_non_bytes_entropy() -> None:
    def invalid_entropy(size: int) -> bytes:
        assert size == 10
        return "not-bytes"  # type: ignore[return-value]

    generator = NodeIdGenerator(clock_ms=fixed_clock(), entropy=invalid_entropy)

    with pytest.raises(TypeError, match="return bytes"):
        generator.mint(NodeIdPrefix.ENGINE)


@pytest.mark.parametrize(
    "value",
    [
        "ABC123",
        "13902",
        "ENG-01ARZ3NDEKTSV4RRFFQ69G5FA",
        "ENG-01ARZ3NDEKTSV4RRFFQ69G5FAV0",
        "BAD-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "eng-01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "ENG_01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "ENG-01arz3ndektsv4rrffq69g5fav",
        "ENG-01ARZ3NDEKTSV4RRFFQ69G5FAI",
        "ENG-81ARZ3NDEKTSV4RRFFQ69G5FAV",
    ],
)
def test_parse_rejects_noncanonical_ids(value: str) -> None:
    with pytest.raises(InvalidNodeIdError):
        parse_node_id(value)

    assert not is_valid_node_id(value)


def test_parse_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="string"):
        parse_node_id(None)  # type: ignore[arg-type]

    assert not is_valid_node_id(None)


def test_convenience_minter_uses_canonical_format() -> None:
    node_id = mint_node_id(NodeIdPrefix.BODY_TYPE)

    assert is_valid_node_id(node_id)
    assert parse_node_id(node_id).prefix is NodeIdPrefix.BODY_TYPE
