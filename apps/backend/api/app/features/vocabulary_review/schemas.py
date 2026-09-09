from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class VocabularyAlignmentRow(BaseModel):
    """One reconciled term pair within a sealed alignment version."""

    source_system: str
    source_term: str
    canonical_term: str
    relation: str
    support: int | None
    evidence_note: str


class VocabularyAlignmentVersion(BaseModel):
    """One sealed, reviewed alignment set for a single vocabulary."""

    alignment_version: str
    vocabulary: str
    activation_note: str
    activated_by: str
    activated_at: datetime
    sealed: bool
    rows: list[VocabularyAlignmentRow]


class VocabularyAlignmentCatalogResponse(BaseModel):
    versions: list[VocabularyAlignmentVersion]


class VocabularyAlignmentDraft(BaseModel):
    """One proposed pair, awaiting or past review."""

    id: int
    vocabulary: str
    source_system: str
    source_term: str
    canonical_term: str
    relation: str
    support: int | None
    evidence_note: str
    status: Literal["proposed", "approved", "declined"]
    proposed_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


class VocabularyAlignmentDraftListResponse(BaseModel):
    drafts: list[VocabularyAlignmentDraft]


class ProposeDraftRequest(BaseModel):
    vocabulary: Literal["fuel", "bodywork", "drive"]
    source_system: Literal["tecdoc", "transportstyrelsen"]
    source_term: str = Field(min_length=1, max_length=120)
    canonical_term: str = Field(min_length=1, max_length=120)
    relation: Literal["equivalent", "compatible"]
    support: int | None = Field(default=None, ge=0)
    evidence_note: str = Field(min_length=5, max_length=1000)
    proposed_by: str = Field(min_length=1, max_length=120)


class ReviewDraftRequest(BaseModel):
    status: Literal["approved", "declined"]
    reviewed_by: str = Field(min_length=1, max_length=120)


class ActivateDraftsRequest(BaseModel):
    vocabulary: Literal["fuel", "bodywork", "drive"]
    alignment_version: str = Field(min_length=1, max_length=120)
    activated_by: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=5, max_length=500)


class ActivateDraftsResponse(BaseModel):
    alignment_version: str
    rows_sealed: int
