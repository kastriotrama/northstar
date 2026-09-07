"""Shared domain utilities for NorthStar services."""

from northstar.alias_identity import (
    ASSERTION_IDENTITY_VERSION,
    build_assertion_identity,
)
from northstar.node_ids import (
    InvalidNodeIdError,
    NodeIdGenerator,
    NodeIdPrefix,
    ParsedNodeId,
    is_valid_node_id,
    mint_node_id,
    parse_node_id,
)

__all__ = [
    "ASSERTION_IDENTITY_VERSION",
    "InvalidNodeIdError",
    "NodeIdGenerator",
    "NodeIdPrefix",
    "ParsedNodeId",
    "build_assertion_identity",
    "is_valid_node_id",
    "mint_node_id",
    "parse_node_id",
]
