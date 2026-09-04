"""Request and response contracts for the chunk review workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ChunkStatus = Literal["open", "proposed", "approved", "rejected", "split"]
Recommendation = Literal[
    "assign_ktype", "split_chunk", "needs_more_evidence", "no_safe_match"
]


class BuildSummary(BaseModel):
    build_id: UUID
    source_batch_id: str
    signature_version: str
    status: str
    row_count: int
    chunk_count: int
    started_at: datetime
    finished_at: datetime | None


class ChunkListItem(BaseModel):
    chunk_id: UUID
    signature: dict[str, Any]
    member_count: int
    reason_profile: dict[str, int]
    status: ChunkStatus


class BuildProgress(BaseModel):
    """Work done on a build, counted build-wide rather than per list filter.

    The two kinds of work are reported apart because they settle different
    questions: a chunk decision says what a group of cars matches, while a
    resolution rule fills a field the register left uninterpretable. A car can
    be counted in both, so they must never be added together.
    """

    decided_rows: int = Field(
        description="Rows in chunks a reviewer approved or sent to be split."
    )
    in_review_rows: int = Field(
        description="Rows in chunks holding a proposal nobody has ruled on yet."
    )
    member_rows: int = Field(description="Rows the build's chunks actually hold.")
    resolved_rows: int = Field(
        description="Cars a resolution rule has filled a field for."
    )
    applied_rules: int = Field(description="Resolution rules currently in force.")


class ChunkPage(BaseModel):
    build: BuildSummary
    total: int
    decided_members: int = Field(
        description="Deprecated alias for `progress.decided_rows`.",
    )
    progress: BuildProgress
    items: list[ChunkListItem]


class MemberSummary(BaseModel):
    source_record_id: int
    source_batch_id: str
    normalization_status: str
    review_reasons: list[str]
    plate: str | None
    source_manufacturer: str | None
    source_model: str | None
    source_year: str | None
    label: str


class PatternChunkRef(BaseModel):
    """One chunk a blocker pattern reaches, and how much of it the pattern covers."""

    chunk_id: UUID
    signature: dict[str, Any]
    member_count: int
    status: ChunkStatus
    overlap_rows: int


class PatternDecisionRecord(BaseModel):
    """A historical pattern-level ruling, shown read-only.

    Pattern decisions are superseded by chunk decisions: `pattern_key` groups
    rows by a hand-built evidence hash, which carries no guarantee that the
    matcher evaluates its members alike. These are kept visible as context, not
    as a decision surface.
    """

    decision_id: str
    action: str
    reviewer: str
    reason: str
    created_at: datetime


class PatternBridge(BaseModel):
    """A blocker pattern resolved onto the chunks that can act on it."""

    operation_id: UUID
    pattern_key: str
    build_id: UUID
    pattern_rows: int
    matched_rows: int
    unmatched_rows: int
    chunks: list[PatternChunkRef]
    history: list[PatternDecisionRecord]


class UnresolvedPopulation(BaseModel):
    source_field: str
    source_value: str
    signature_field: str
    row_count: int


class UnresolvedOverview(BaseModel):
    build_id: UUID
    populations: list[UnresolvedPopulation]


class DiscriminatorField(BaseModel):
    field: str
    distinct_count: int
    present_count: int
    coverage: float
    separation: float
    concision: float
    score: float
    usable: bool
    top_values: list[FieldValueCount]
    constrained: bool = Field(
        default=False,
        description=(
            "True when the rule already tests this field. Its counts are then "
            "computed with its own clause lifted, so the values it does not "
            "yet cover stay visible and can be OR-ed in."
        ),
    )
    selected_values: list[str] = Field(
        default_factory=list,
        description="Values of this field the rule already covers.",
    )


class DiscriminatorReport(BaseModel):
    build_id: UUID
    source_field: str
    source_value: str
    signature_field: str
    population: int
    fields: list[DiscriminatorField]


class RuleCondition(BaseModel):
    """One clause. Values are OR-ed; clauses are AND-ed together."""

    field: str = Field(min_length=1, max_length=60)
    value: str | None = Field(default=None, max_length=200)
    values: list[str] | None = Field(default=None, max_length=50)
    layer: Literal["source", "normalized"] = Field(
        default="source",
        description=(
            "`source` matches the registry string verbatim; `normalized` "
            "matches the canonical value derived from it."
        ),
    )
    operator: Literal[
        "equals", "not_equals", "starts_with", "contains", "gte", "lte"
    ] = "equals"

    @model_validator(mode="after")
    def _require_terms(self) -> RuleCondition:
        if not self.terms:
            raise ValueError("condition needs `value` or a non-empty `values`")
        if self.operator in {"gte", "lte"} and len(self.terms) != 1:
            raise ValueError(f"{self.operator} takes exactly one value")
        return self

    @property
    def terms(self) -> tuple[str, ...]:
        source = self.values if self.values else ([self.value] if self.value else [])
        return tuple(item for item in source if item and item.strip())


class RulePreviewRequest(BaseModel):
    build_id: UUID
    conditions: list[RuleCondition] = Field(min_length=1, max_length=6)
    target_field: str = Field(min_length=1, max_length=60)
    target_value: str = Field(min_length=1, max_length=80)


class RulePreview(BaseModel):
    conditions: list[RuleCondition]
    target_field: str
    target_value: str
    matched_rows: int
    would_resolve: int
    already_resolved: int
    sample_plates: list[str]


class ResolutionRuleRequest(BaseModel):
    """A previewed rule a reviewer wants kept."""

    build_id: UUID
    source_field: str = Field(min_length=1, max_length=60)
    source_value: str = Field(min_length=1, max_length=200)
    conditions: list[RuleCondition] = Field(min_length=1, max_length=6)
    target_field: str = Field(min_length=1, max_length=60)
    target_value: str = Field(min_length=1, max_length=80)
    author: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class ResolutionRuleActionRequest(BaseModel):
    """Who is running or retiring a saved rule."""

    reviewer: str = Field(min_length=1, max_length=120)


class ResolutionRule(BaseModel):
    """A saved rule and what running it has done so far.

    The counts split deliberately: `would_resolve` is what the preview promised
    when the rule was saved, `resolved_rows` is what running it actually wrote.
    They differ whenever the population moved in between — another rule got
    there first, or a rebuild filled the gap.
    """

    rule_id: UUID
    build_id: UUID
    source_field: str
    source_value: str
    target_field: str
    target_value: str
    conditions: list[RuleCondition]
    author: str
    note: str | None
    matched_rows: int
    would_resolve: int
    already_resolved: int
    status: Literal["saved", "applied", "retired"]
    resolved_rows: int
    created_at: datetime
    applied_at: datetime | None
    applied_by: str | None
    retired_at: datetime | None
    retired_by: str | None
    resolved_now: int | None = Field(
        default=None,
        description="Rows this run wrote; null unless the call ran the rule.",
    )
    superseded_rows: int | None = Field(
        default=None,
        description="Rows this call reopened; null unless the call retired it.",
    )


class ComparisonRow(BaseModel):
    field: str
    source_field: str | None
    resolvable: bool = Field(
        default=False,
        description=(
            "True when this source field has an unresolved-population view, so "
            "the screen can link straight to authoring a rule for it."
        ),
    )
    status: Literal["resolved", "unresolved", "missing"]
    source_value: str | None
    normalized_value: str | None
    oem_value: str | None
    conflict: bool | None = Field(
        description=(
            "True when normalized and OEM evidence disagree; null while no "
            "OEM evidence exists for this vehicle."
        )
    )


class FieldValueCount(BaseModel):
    value: str
    count: int
    meaning: str | None = Field(
        default=None,
        description="What the register means by this code, when it defines one.",
    )


class FieldVariance(BaseModel):
    field: str
    distinct_count: int
    present_count: int
    uniform: bool
    top_values: list[FieldValueCount]


class ChunkFieldProfile(BaseModel):
    chunk_id: UUID
    member_count: int
    scanned_members: int
    truncated: bool = Field(
        description="True when the chunk is larger than the scan limit."
    )
    varying_fields: list[str]
    fields: list[FieldVariance]


class MemberComparison(BaseModel):
    source_record_id: int
    label: str
    plate: str | None
    has_oem_evidence: bool
    rows: list[ComparisonRow]


class OemSampleSummary(BaseModel):
    sample_id: int
    source_record_id: int
    provider: str
    masked_vin: str
    dataset_version: str
    fetched_at: datetime
    reused_cached_evidence: bool
    response_payload: dict[str, Any]


class ProposalSummary(BaseModel):
    proposal_id: UUID
    proposal_source: Literal["heuristic", "agent", "human"]
    adjudicator_version: str
    recommendation: Recommendation
    target_ktype_reference: str | None
    confidence: float
    evidence: dict[str, Any]
    reasoning: str
    status: Literal["proposed", "approved", "rejected"]
    reviewed_by: str | None
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime


class ChunkDetail(BaseModel):
    chunk_id: UUID
    build_id: UUID
    signature: dict[str, Any]
    member_count: int
    reason_profile: dict[str, int]
    status: ChunkStatus
    members: list[MemberSummary]
    oem_samples: list[OemSampleSummary]
    proposals: list[ProposalSummary]


class OemSampleRequest(BaseModel):
    source_record_id: int = Field(ge=1)
    request_id: UUID = Field(
        description="Caller-issued idempotency key reused across retries."
    )


class ProposalReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class PopulationAttribute(BaseModel):
    field: str
    distinct_count: int
    present_count: int
    top_values: list[FieldValueCount]


class PopulationAttributes(BaseModel):
    """Every source key present in the population, for free-form picking."""

    build_id: UUID
    source_field: str
    source_value: str
    population: int
    scanned_members: int
    sampled: bool = Field(
        description="True when counts come from a sample, not the full population."
    )
    attributes: list[PopulationAttribute]


class ValuePatternSuggestion(BaseModel):
    prefix: str
    row_count: int
    distinct_values: int
    coverage: float
    score: float


class PatternReport(BaseModel):
    field: str
    population: int
    patterns: list[ValuePatternSuggestion]


class RuleAdviceRequest(BaseModel):
    build_id: UUID
    source_field: str = Field(min_length=1, max_length=60)
    source_value: str = Field(min_length=1, max_length=200)


class RuleAdvice(BaseModel):
    advisor: str
    confident: bool
    conditions: list[RuleCondition]
    target_field: str
    target_value: str | None
    reasoning: str
    evidence: dict[str, Any]


class TargetVocabulary(BaseModel):
    """Allowed values for a rule target.

    `closed` means the value must come from `values`; otherwise `values` are
    suggestions and free text is accepted.
    """

    target_field: str
    closed: bool
    values: list[FieldValueCount]
    source: Literal["reviewed_rules", "observed", "none"]


class NarrowingStep(BaseModel):
    label: str
    matched_rows: int


class RefineRequest(BaseModel):
    build_id: UUID
    source_field: str = Field(min_length=1, max_length=60)
    source_value: str = Field(min_length=1, max_length=200)
    conditions: list[RuleCondition] = Field(min_length=1, max_length=6)


class RefineResult(BaseModel):
    """Live state of a rule being narrowed: how many, what is left, are we done."""

    matched_rows: int
    would_resolve: int
    already_resolved: int
    signature_field: str
    homogeneous: bool = Field(
        description=(
            "True when no identity-bearing field still varies, so the matched "
            "cars can be treated as one thing."
        )
    )
    varying_identity_fields: list[str]
    trail: list[NarrowingStep]
    fields: list[DiscriminatorField]
