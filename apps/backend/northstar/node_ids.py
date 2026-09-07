"""Mint and validate source-independent graph node identifiers."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_LENGTH = 26
ULID_ENTROPY_BYTES = 10
NODE_ID_SEPARATOR = "-"
PREFIX_LENGTH = 3
MAX_ULID_TIMESTAMP_MS = (1 << 48) - 1

_ULID_BITS = 128
_ENTROPY_BITS = ULID_ENTROPY_BYTES * 8
_CROCKFORD_VALUES = {character: index for index, character in enumerate(CROCKFORD_ALPHABET)}

ClockMilliseconds = Callable[[], int]
EntropySource = Callable[[int], bytes]


class NodeIdPrefix(StrEnum):
    """Accepted prefixes for the nine canonical graph node labels."""

    MANUFACTURER = "MFR"
    MODEL_FAMILY = "FAM"
    PLATFORM = "PLT"
    ENGINE = "ENG"
    TRANSMISSION = "TRN"
    BODY_TYPE = "BDY"
    FUEL_TYPE = "FUL"
    VEHICLE_VARIANT = "VEH"
    ALIAS = "ALI"


class InvalidNodeIdError(ValueError):
    """Raised when a value is not a canonical prefixed ULID."""


@dataclass(frozen=True)
class ParsedNodeId:
    """Validated components of a canonical graph node ID."""

    prefix: NodeIdPrefix
    ulid: str
    timestamp_ms: int


def _system_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def _coerce_prefix(prefix: NodeIdPrefix | str) -> NodeIdPrefix:
    if isinstance(prefix, NodeIdPrefix):
        return prefix
    if not isinstance(prefix, str):
        raise TypeError("Node ID prefix must be a NodeIdPrefix or string")
    try:
        return NodeIdPrefix(prefix)
    except ValueError as exc:
        raise ValueError(f"Unsupported node ID prefix: {prefix!r}") from exc


def _validate_timestamp_ms(timestamp_ms: int) -> None:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise TypeError("ULID timestamp must be an integer number of milliseconds")
    if not 0 <= timestamp_ms <= MAX_ULID_TIMESTAMP_MS:
        raise ValueError(
            f"ULID timestamp must be between 0 and {MAX_ULID_TIMESTAMP_MS} milliseconds"
        )


def _validate_entropy(entropy: bytes) -> None:
    if not isinstance(entropy, bytes):
        raise TypeError("ULID entropy source must return bytes")
    if len(entropy) != ULID_ENTROPY_BYTES:
        raise ValueError(f"ULID entropy source must return exactly {ULID_ENTROPY_BYTES} bytes")


def _encode_ulid(timestamp_ms: int, entropy: bytes) -> str:
    _validate_timestamp_ms(timestamp_ms)
    _validate_entropy(entropy)

    value = (timestamp_ms << _ENTROPY_BITS) | int.from_bytes(entropy, byteorder="big")
    encoded = [CROCKFORD_ALPHABET[0]] * ULID_LENGTH
    for index in range(ULID_LENGTH - 1, -1, -1):
        encoded[index] = CROCKFORD_ALPHABET[value & 0b11111]
        value >>= 5
    return "".join(encoded)


def _decode_ulid(ulid: str) -> int:
    if len(ulid) != ULID_LENGTH:
        raise InvalidNodeIdError(f"ULID payload must contain exactly {ULID_LENGTH} characters")

    value = 0
    for character in ulid:
        try:
            digit = _CROCKFORD_VALUES[character]
        except KeyError as exc:
            raise InvalidNodeIdError(
                "ULID payload must use uppercase Crockford Base32 without I, L, O, or U"
            ) from exc
        value = (value << 5) | digit

    if value >= 1 << _ULID_BITS:
        raise InvalidNodeIdError("ULID payload exceeds 128 bits")
    return value


class NodeIdGenerator:
    """Stateless prefixed-ULID generator with injectable time and entropy."""

    def __init__(
        self,
        *,
        clock_ms: ClockMilliseconds | None = None,
        entropy: EntropySource | None = None,
    ) -> None:
        self._clock_ms = clock_ms or _system_clock_ms
        self._entropy = entropy or secrets.token_bytes

    def mint(self, prefix: NodeIdPrefix | str) -> str:
        """Return a new canonical ID for an accepted graph node prefix."""

        canonical_prefix = _coerce_prefix(prefix)
        timestamp_ms = self._clock_ms()
        entropy = self._entropy(ULID_ENTROPY_BYTES)
        ulid = _encode_ulid(timestamp_ms, entropy)
        return f"{canonical_prefix.value}{NODE_ID_SEPARATOR}{ulid}"


def mint_node_id(prefix: NodeIdPrefix | str) -> str:
    """Mint a canonical node ID using the system clock and secure entropy."""

    return NodeIdGenerator().mint(prefix)


def parse_node_id(value: str) -> ParsedNodeId:
    """Validate a canonical node ID and return its typed components."""

    if not isinstance(value, str):
        raise TypeError("Node ID must be a string")

    expected_length = PREFIX_LENGTH + len(NODE_ID_SEPARATOR) + ULID_LENGTH
    if len(value) != expected_length:
        raise InvalidNodeIdError(f"Node ID must contain exactly {expected_length} characters")
    if value[PREFIX_LENGTH] != NODE_ID_SEPARATOR:
        raise InvalidNodeIdError("Node ID must separate its prefix and ULID with one hyphen")

    prefix_text = value[:PREFIX_LENGTH]
    try:
        prefix = NodeIdPrefix(prefix_text)
    except ValueError as exc:
        raise InvalidNodeIdError(f"Unsupported node ID prefix: {prefix_text!r}") from exc

    ulid = value[PREFIX_LENGTH + len(NODE_ID_SEPARATOR) :]
    decoded = _decode_ulid(ulid)
    return ParsedNodeId(prefix=prefix, ulid=ulid, timestamp_ms=decoded >> _ENTROPY_BITS)


def is_valid_node_id(value: object) -> bool:
    """Return whether a value is a canonical prefixed ULID."""

    if not isinstance(value, str):
        return False
    try:
        parse_node_id(value)
    except InvalidNodeIdError:
        return False
    return True
