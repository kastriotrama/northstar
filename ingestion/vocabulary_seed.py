"""The reviewed vocabulary alignment sets, as reproducible data.

An activated alignment set is immutable, so the initial set ships here rather
than as a hand-run INSERT: a database restored anywhere must be able to reach
the same pinned state, and a match run pinned to `align-2026-08-28-v1` must
mean the same thing on every machine.

Corrections never edit these rows. Add a new version below, activate it, and
pin runs to it -- the immutability trigger enforces that for us.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg import Connection

from ingestion.vocabulary_migrations import (
    VOCABULARY_ALIGNMENT_TABLE,
    VOCABULARY_ALIGNMENT_VERSION_TABLE,
)

INITIAL_FUEL_ALIGNMENT_VERSION = "align-2026-08-28-v1"


@dataclass(frozen=True)
class SeedRow:
    vocabulary: str
    source_system: str
    source_term: str
    canonical_term: str
    relation: str
    support: int | None
    evidence_note: str


# Transportstyrelsen and TecDoc are each normalized into our own token set by
# independent code paths that agree on petrol and diesel and disagree elsewhere.
# Every row below reconciles two of *our* canonicalisations; neither vendor's
# data is altered.
INITIAL_FUEL_ALIGNMENT: tuple[SeedRow, ...] = (
    SeedRow(
        vocabulary="fuel",
        source_system="transportstyrelsen",
        source_term="electricity",
        canonical_term="electric",
        relation="equivalent",
        support=None,
        evidence_note=(
            "Same concept, different canonicalisation. TecDoc KT-088 label "
            "'Electric' becomes 'electric'; the registry rules produce "
            "'electricity'. Disjoint sets, so no electric vehicle could "
            "intersect any of the 551 electric KTypes."
        ),
    ),
    SeedRow(
        vocabulary="fuel",
        source_system="transportstyrelsen",
        source_term="methane",
        canonical_term="cng",
        relation="equivalent",
        support=None,
        evidence_note="TecDoc folds Natural Gas and Biogas into cng.",
    ),
    SeedRow(
        vocabulary="fuel",
        source_system="transportstyrelsen",
        source_term="ethanol",
        canonical_term="petrol",
        relation="compatible",
        support=627,
        evidence_note=(
            "TecDoc folds E85/E10/E5 blends into petrol. A granularity "
            "difference rather than a synonym, so it must score neutral and "
            "never as agreement."
        ),
    ),
)

SEED_SETS: dict[str, tuple[tuple[SeedRow, ...], str]] = {
    INITIAL_FUEL_ALIGNMENT_VERSION: (
        INITIAL_FUEL_ALIGNMENT,
        "Initial fuel alignment reconciling the TS and TecDoc canonicalisations.",
    ),
}


def apply_vocabulary_seed(
    connection: Connection,
    *,
    alignment_version: str = INITIAL_FUEL_ALIGNMENT_VERSION,
    activated_by: str,
) -> dict[str, int]:
    """Activate one alignment set idempotently, refusing a changed definition.

    Re-running is a no-op. A version already present with different content is
    an error rather than a silent divergence: runs pinned to it would otherwise
    mean different things on different machines.
    """

    if alignment_version not in SEED_SETS:
        raise ValueError(f"unknown alignment version: {alignment_version!r}")
    rows, note = SEED_SETS[alignment_version]

    with connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VOCABULARY_ALIGNMENT_VERSION_TABLE} "
            "(alignment_version, activation_note, activated_by, sealed) "
            "VALUES (%s, %s, %s, FALSE) "
            "ON CONFLICT (alignment_version) DO NOTHING",
            (alignment_version, note, activated_by),
        )
        created_version = cursor.rowcount == 1
        cursor.execute(
            f"SELECT sealed FROM {VOCABULARY_ALIGNMENT_VERSION_TABLE} "
            "WHERE alignment_version = %s FOR UPDATE", (alignment_version,),
        )
        version_row = cursor.fetchone()
        if version_row is None:
            raise ValueError("vocabulary version disappeared during activation")
        sealed = bool(version_row[0])

        cursor.execute(
            "SELECT vocabulary, source_system, source_term, canonical_term, relation, "
            "support, evidence_note "
            f"FROM {VOCABULARY_ALIGNMENT_TABLE} WHERE alignment_version = %s",
            (alignment_version,),
        )
        existing = {
            (str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4]), r[5], r[6])
            for r in cursor.fetchall()
        }
        expected = {
            (r.vocabulary, r.source_system, r.source_term, r.canonical_term,
             r.relation, r.support, r.evidence_note)
            for r in rows
        }
        if (existing or sealed) and existing != expected:
            raise ValueError(
                f"alignment version {alignment_version!r} already exists with "
                "different rows; activate a new version instead"
            )

        inserted = 0
        for row in (() if sealed else rows):
            cursor.execute(
                f"INSERT INTO {VOCABULARY_ALIGNMENT_TABLE} "
                "(alignment_version, vocabulary, source_system, source_term, "
                "canonical_term, relation, support, evidence_note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (alignment_version, vocabulary, source_system, "
                "source_term, canonical_term) DO NOTHING",
                (
                    alignment_version,
                    row.vocabulary,
                    row.source_system,
                    row.source_term,
                    row.canonical_term,
                    row.relation,
                    row.support,
                    row.evidence_note,
                ),
            )
            inserted += cursor.rowcount
        if not sealed:
            cursor.execute(
                f"UPDATE {VOCABULARY_ALIGNMENT_VERSION_TABLE} SET sealed = TRUE "
                "WHERE alignment_version = %s", (alignment_version,),
            )
    connection.commit()
    return {
        "alignment_version_created": int(created_version),
        "rows_inserted": inserted,
        "rows_total": len(rows),
    }
