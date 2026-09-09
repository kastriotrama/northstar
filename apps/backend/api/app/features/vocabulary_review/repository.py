from __future__ import annotations

from datetime import datetime, timezone

from api.app.features.normalization_review.repository import ConnectionFactory
from ingestion.vocabulary_migrations import (
    VOCABULARY_ALIGNMENT_DRAFTS_TABLE,
    VOCABULARY_ALIGNMENT_TABLE,
    VOCABULARY_ALIGNMENT_VERSION_TABLE,
    run_vocabulary_migrations,
)


class VocabularyReviewError(RuntimeError):
    """Raised when a draft or activation request cannot be applied safely."""


class VocabularyReviewRepository:
    """Sealed content is read-only here; only drafts are ever mutated.

    Authoring a *sealed* version stays where it already lives --
    `vocabulary_seed.py` and `northstar-ingest promote-vocabulary-alignments`
    -- so a reviewer always sees exactly what was actually activated. Drafts
    are the new part: a proposed pair a reviewer approves or declines before
    it can ever reach that sealed table.
    """

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self._connection_factory() as connection:
            run_vocabulary_migrations(connection)

    def fetch_versions(self) -> list[dict]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"SELECT alignment_version, activation_note, activated_by, "
                f"activated_at, sealed FROM {VOCABULARY_ALIGNMENT_VERSION_TABLE} "
                "ORDER BY activated_at DESC"
            )
            versions = cursor.fetchall()

            cursor.execute(
                f"SELECT alignment_version, vocabulary, source_system, source_term, "
                f"canonical_term, relation, support, evidence_note "
                f"FROM {VOCABULARY_ALIGNMENT_TABLE} "
                "ORDER BY alignment_version, vocabulary, source_system, source_term"
            )
            rows = cursor.fetchall()

        rows_by_version: dict[str, list[dict]] = {}
        vocabulary_by_version: dict[str, str] = {}
        for row in rows:
            (
                alignment_version, vocabulary, source_system, source_term,
                canonical_term, relation, support, evidence_note,
            ) = row
            rows_by_version.setdefault(str(alignment_version), []).append({
                "source_system": str(source_system),
                "source_term": str(source_term),
                "canonical_term": str(canonical_term),
                "relation": str(relation),
                "support": support,
                "evidence_note": str(evidence_note),
            })
            # A sealed version carries exactly one vocabulary (enforced at
            # promotion time), so the first row's value is the only value.
            vocabulary_by_version.setdefault(str(alignment_version), str(vocabulary))

        return [
            {
                "alignment_version": str(version[0]),
                "vocabulary": vocabulary_by_version.get(str(version[0]), ""),
                "activation_note": str(version[1]),
                "activated_by": str(version[2]),
                "activated_at": version[3],
                "sealed": bool(version[4]),
                "rows": rows_by_version.get(str(version[0]), []),
            }
            for version in versions
        ]

    def fetch_drafts(self) -> list[dict]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, vocabulary, source_system, source_term, canonical_term, "
                "relation, support, evidence_note, status, proposed_by, reviewed_by, "
                f"reviewed_at, created_at FROM {VOCABULARY_ALIGNMENT_DRAFTS_TABLE} "
                "ORDER BY vocabulary, status, created_at"
            )
            rows = cursor.fetchall()
        return [self._draft_row(row) for row in rows]

    @staticmethod
    def _draft_row(row: tuple) -> dict:
        return {
            "id": int(row[0]),
            "vocabulary": str(row[1]),
            "source_system": str(row[2]),
            "source_term": str(row[3]),
            "canonical_term": str(row[4]),
            "relation": str(row[5]),
            "support": row[6],
            "evidence_note": str(row[7]),
            "status": str(row[8]),
            "proposed_by": str(row[9]),
            "reviewed_by": row[10],
            "reviewed_at": row[11],
            "created_at": row[12],
        }

    def propose_draft(
        self,
        *,
        vocabulary: str,
        source_system: str,
        source_term: str,
        canonical_term: str,
        relation: str,
        support: int | None,
        evidence_note: str,
        proposed_by: str,
    ) -> dict:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    f"INSERT INTO {VOCABULARY_ALIGNMENT_DRAFTS_TABLE} "
                    "(vocabulary, source_system, source_term, canonical_term, relation, "
                    "support, evidence_note, proposed_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING id, vocabulary, source_system, source_term, canonical_term, "
                    "relation, support, evidence_note, status, proposed_by, reviewed_by, "
                    "reviewed_at, created_at",
                    (
                        vocabulary, source_system, source_term, canonical_term,
                        relation, support, evidence_note, proposed_by,
                    ),
                )
            except Exception as error:  # noqa: BLE001
                connection.rollback()
                if "vocabulary_alignment_draft_unique_pending" in str(error):
                    raise VocabularyReviewError(
                        "a draft or approved row already proposes this exact pair"
                    ) from error
                raise
            row = cursor.fetchone()
            connection.commit()
        return self._draft_row(row)

    def review_draft(self, draft_id: int, *, status: str, reviewed_by: str) -> dict:
        if status not in {"approved", "declined"}:
            raise VocabularyReviewError("status must be 'approved' or 'declined'")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {VOCABULARY_ALIGNMENT_DRAFTS_TABLE} "
                "SET status = %s, reviewed_by = %s, reviewed_at = %s "
                "WHERE id = %s AND status = 'proposed' "
                "RETURNING id, vocabulary, source_system, source_term, canonical_term, "
                "relation, support, evidence_note, status, proposed_by, reviewed_by, "
                "reviewed_at, created_at",
                (status, reviewed_by, datetime.now(timezone.utc), draft_id),
            )
            row = cursor.fetchone()
            if row is None:
                connection.rollback()
                raise VocabularyReviewError(
                    f"draft {draft_id} does not exist or is no longer pending review"
                )
            connection.commit()
        return self._draft_row(row)

    def activate_approved(
        self, *, vocabulary: str, alignment_version: str, activated_by: str, note: str
    ) -> dict:
        """Seal every currently-approved draft for one vocabulary into a new version.

        Immutable from here on, same guarantee as a code-seeded version: the
        insert trigger only accepts rows while the version is unsealed, and
        the version guard only allows the one sealing transition afterwards.
        """

        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, source_system, source_term, canonical_term, relation, "
                f"support, evidence_note FROM {VOCABULARY_ALIGNMENT_DRAFTS_TABLE} "
                "WHERE vocabulary = %s AND status = 'approved' ORDER BY id",
                (vocabulary,),
            )
            approved = cursor.fetchall()
            if not approved:
                connection.rollback()
                raise VocabularyReviewError(
                    f"no approved drafts for vocabulary {vocabulary!r}"
                )

            cursor.execute(
                f"INSERT INTO {VOCABULARY_ALIGNMENT_VERSION_TABLE} "
                "(alignment_version, activation_note, activated_by, sealed) "
                "VALUES (%s, %s, %s, FALSE) "
                "ON CONFLICT (alignment_version) DO NOTHING",
                (alignment_version, note, activated_by),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise VocabularyReviewError(
                    f"alignment version {alignment_version!r} already exists"
                )

            draft_ids = [int(row[0]) for row in approved]
            for row in approved:
                (
                    _draft_id, source_system, source_term, canonical_term,
                    relation, support, evidence_note,
                ) = row
                cursor.execute(
                    f"INSERT INTO {VOCABULARY_ALIGNMENT_TABLE} "
                    "(alignment_version, vocabulary, source_system, source_term, "
                    "canonical_term, relation, support, evidence_note) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        alignment_version, vocabulary, source_system, source_term,
                        canonical_term, relation, support, evidence_note,
                    ),
                )

            cursor.execute(
                f"UPDATE {VOCABULARY_ALIGNMENT_VERSION_TABLE} SET sealed = TRUE "
                "WHERE alignment_version = %s",
                (alignment_version,),
            )
            # The drafts did their job; remove them so 'approved' always means
            # "waiting to be sealed", never "already sealed, still sitting here".
            cursor.execute(
                f"DELETE FROM {VOCABULARY_ALIGNMENT_DRAFTS_TABLE} WHERE id = ANY(%s)",
                (draft_ids,),
            )
            connection.commit()
        return {"alignment_version": alignment_version, "rows_sealed": len(draft_ids)}
