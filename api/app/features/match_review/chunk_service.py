"""Orchestration for chunk browsing, OEM sampling, and proposal review."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID, uuid4

from api.app.features.match_review.adjudicator import (
    IDENTITY_FIELDS,
    MatchAdjudicator,
)
from api.app.features.match_review.chunk_schemas import (
    BuildSummary,
    ChunkDetail,
    ChunkFieldProfile,
    ChunkListItem,
    ChunkPage,
    ComparisonRow,
    DiscriminatorField,
    DiscriminatorReport,
    FieldValueCount,
    FieldVariance,
    MemberComparison,
    MemberSummary,
    NarrowingStep,
    OemSampleRequest,
    OemSampleSummary,
    PatternReport,
    PopulationAttribute,
    PopulationAttributes,
    ProposalReviewRequest,
    ProposalSummary,
    RefineRequest,
    RefineResult,
    RuleAdvice,
    RuleAdviceRequest,
    RuleCondition,
    RulePreview,
    RulePreviewRequest,
    TargetVocabulary,
    UnresolvedOverview,
    UnresolvedPopulation,
    ValuePatternSuggestion,
)
from api.app.features.match_review.field_resolution import (
    CANDIDATE_DISCRIMINATORS,
    RESOLVABLE_TARGETS,
    SIGNATURE_FIELDS,
    SOURCE_TO_SIGNATURE,
    PredicateTerm,
    describe_value,
    field_status,
    score_discriminator,
    suggest_value_patterns,
)
from api.app.features.match_review.integrations import OemVinProvider, mask_vin
from api.app.features.match_review.rule_advisor import RuleAdvisor

MEMBER_PREVIEW_LIMIT = 25
ATTRIBUTE_SCAN_LIMIT = 20_000
ADVISOR_VALUE_FIELDS = 3
FIELD_PROFILE_SCAN_LIMIT = 5_000

# Raw Transportstyrelsen fields whose spread the normalized signature hides.
# `brand`/`model_no`/`type_text` in particular carry the model identity that
# normalization could not resolve.
PROFILED_SOURCE_FIELDS: tuple[str, ...] = (
    "brand",
    "manufacturer",
    "model",
    "model_no",
    "variant",
    "version",
    "type_text",
    "fab_code",
    "eeg_type_approval",
    "body_code",
    "fuel1",
    "kw",
    "ccm",
    "is_4wd",
    "vehicle_year",
)

_VIN_KEYS = frozenset({"vin", "vin_number", "vehicle_identification_number"})


class ChunkRepository(Protocol):
    def ensure_schema(self) -> None: ...
    def fetch_builds(self, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def fetch_build(self, build_id: UUID) -> dict[str, Any] | None: ...
    def fetch_latest_build(self) -> dict[str, Any] | None: ...
    def fetch_chunk_page(
        self,
        *,
        build_id: UUID,
        status: str | None,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[int, int, list[dict[str, Any]]]: ...
    def fetch_chunk(self, chunk_id: UUID) -> dict[str, Any] | None: ...
    def fetch_members(
        self, chunk_id: UUID, *, limit: int = 25
    ) -> list[dict[str, Any]]: ...
    def fetch_member_evidence(
        self, chunk_id: UUID, source_record_id: int
    ) -> dict[str, Any] | None: ...
    def fetch_field_profile(
        self,
        chunk_id: UUID,
        *,
        fields: tuple[str, ...],
        sample_limit: int = 5_000,
        top_values: int = 5,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_unresolved_populations(
        self,
        build_id: UUID,
        *,
        source_field: str,
        signature_field: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...
    def fetch_discriminators(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        candidate_fields: tuple[str, ...],
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_signature_values(
        self, build_id: UUID, *, signature_field: str, limit: int = 60
    ) -> list[dict[str, Any]]: ...
    def fetch_population_oem_samples(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...
    def fetch_refined_discriminators(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
        candidate_fields: tuple[str, ...],
        top_values: int = 8,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_narrowing_trail(
        self,
        build_id: UUID,
        *,
        signature_field: str,
        conditions: list[PredicateTerm],
    ) -> list[int]: ...
    def preview_rule(
        self,
        build_id: UUID,
        *,
        conditions: list[PredicateTerm],
        signature_field: str,
        sample_limit: int = 5,
    ) -> dict[str, Any]: ...
    def fetch_population_attributes(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        signature_field: str,
        top_values: int = 6,
        sample_limit: int = 20_000,
    ) -> tuple[int, list[dict[str, Any]]]: ...
    def fetch_member_vin(
        self, chunk_id: UUID, source_record_id: int
    ) -> str | None: ...
    def fetch_oem_evidence(
        self, *, provider: str, vin: str, dataset_version: str
    ) -> dict[str, Any] | None: ...
    def insert_oem_evidence(
        self,
        *,
        request_id: UUID,
        provider: str,
        vin: str,
        dataset_version: str,
        response_payload: dict[str, Any],
    ) -> dict[str, Any]: ...
    def link_sample(
        self, *, chunk_id: UUID, evidence_id: int, source_record_id: int
    ) -> None: ...
    def fetch_samples(self, chunk_id: UUID) -> list[dict[str, Any]]: ...
    def insert_proposal(
        self,
        *,
        proposal_id: UUID,
        chunk_id: UUID,
        proposal_source: str,
        adjudicator_version: str,
        recommendation: str,
        target_ktype_reference: str | None,
        confidence: float,
        evidence: dict[str, Any],
        reasoning: str,
    ) -> dict[str, Any]: ...
    def fetch_proposal(self, proposal_id: UUID) -> dict[str, Any] | None: ...
    def fetch_proposals(self, chunk_id: UUID) -> list[dict[str, Any]]: ...
    def review_proposal(
        self,
        *,
        proposal_id: UUID,
        status: str,
        chunk_status: str,
        reviewer: str,
        note: str | None,
    ) -> dict[str, Any] | None: ...


class MatchReviewNotFoundError(LookupError):
    pass


class MatchReviewConflictError(RuntimeError):
    pass


class MemberVinUnavailableError(LookupError):
    pass


_OPERATOR_LABELS = {
    "equals": "=",
    "not_equals": "≠",
    "starts_with": "starts with",
    "contains": "contains",
    "gte": "≥",
    "lte": "≤",
}


def _condition_label(condition: Any) -> str:
    operator = _OPERATOR_LABELS.get(condition.operator, condition.operator)
    return f"{condition.field} {operator} {' or '.join(condition.terms)}"


def _value_counts(field: str, items: list[dict[str, Any]]) -> list[FieldValueCount]:
    """Attach the register's own meaning to each raw value, where one exists."""

    return [
        FieldValueCount(
            value=item["value"],
            count=item["count"],
            meaning=describe_value(field, item["value"]),
        )
        for item in items
    ]


def _member_label(member: dict[str, Any]) -> str:
    parts = [
        value
        for value in (
            member.get("plate"),
            member.get("source_manufacturer"),
            member.get("source_model"),
            member.get("source_year"),
        )
        if value
    ]
    if parts:
        return " · ".join(str(part) for part in parts)
    return f"Record {member['source_record_id']}"


def _first_with_key(mapping: Any, *keys: str) -> tuple[str | None, str | None]:
    """Return the first populated value and the key it actually came from.

    Several comparison rows read more than one registry key (manufacturer
    falls back to brand, year to model_year), so the key that supplied the
    value is part of the evidence — naming the wrong one would misdirect
    anyone tracing a value back to the register.
    """

    for key in keys:
        value = _first(mapping, key)
        if value is not None:
            return value, key
    return None, (keys[0] if keys else None)


def _first(mapping: Any, *keys: str) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            joined = ", ".join(str(item) for item in value if item is not None)
            if joined:
                return joined
            continue
        text = str(value).strip()
        if text:
            return text
    return None


# (label, TS raw keys, normalized keys, OEM payload keys)
_COMPARISON_FIELDS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("Manufacturer", ("manufacturer", "brand"), ("manufacturer",), ("manufacturer", "make")),
    ("Model", ("model",), ("model_family",), ("model", "model_name")),
    ("Variant", ("variant",), (), ("variant", "trim")),
    ("Version", ("version",), (), ("version",)),
    ("Type text", ("type_text",), (), ()),
    ("Year", ("vehicle_year", "model_year"), ("production_year",), ("model_year", "year")),
    ("Fuel", ("fuel1", "fuel_combo"), ("energy_sources",), ("fuel", "fuel_type")),
    ("Power (kW)", ("kw",), ("power_kw",), ("power_kw",)),
    ("Displacement (cc)", ("ccm",), ("displacement_cc",), ("displacement_cc", "displacement")),
    ("Drive", ("is_4wd",), ("drive_type",), ("drive", "drive_type")),
    ("Body", ("body_code",), ("bodywork_form",), ("body", "body_type")),
)


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Mask VIN-bearing keys so full VINs never leave the server."""

    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _VIN_KEYS and isinstance(value, str):
            sanitized[key] = mask_vin(value)
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_payload(value)
        else:
            sanitized[key] = value
    return sanitized


class MatchReviewService:
    def __init__(
        self,
        repository: ChunkRepository,
        *,
        oem_provider: OemVinProvider,
        adjudicator: MatchAdjudicator,
        rule_advisor: RuleAdvisor | None = None,
        proposal_source: str = "heuristic",
    ) -> None:
        self._repository = repository
        self._oem_provider = oem_provider
        self._adjudicator = adjudicator
        self._rule_advisor = rule_advisor
        self._proposal_source = proposal_source
        repository.ensure_schema()

    def list_builds(self) -> list[BuildSummary]:
        return [
            BuildSummary(**build) for build in self._repository.fetch_builds()
        ]

    def list_chunks(
        self,
        *,
        build_id: UUID | None,
        status: str | None,
        query: str,
        limit: int,
        offset: int,
    ) -> ChunkPage:
        if build_id is None:
            build = self._repository.fetch_latest_build()
            if build is None:
                raise MatchReviewNotFoundError(
                    "No completed chunk build exists yet; run build-match-chunks."
                )
        else:
            build = self._repository.fetch_build(build_id)
            if build is None:
                raise MatchReviewNotFoundError(f"Unknown build {build_id}")
        total, decided_members, items = self._repository.fetch_chunk_page(
            build_id=build["build_id"],
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
        return ChunkPage(
            build=BuildSummary(**build),
            total=total,
            decided_members=decided_members,
            items=[ChunkListItem(**item) for item in items],
        )

    def get_chunk(self, chunk_id: UUID) -> ChunkDetail:
        chunk = self._require_chunk(chunk_id)
        members = self._repository.fetch_members(
            chunk_id, limit=MEMBER_PREVIEW_LIMIT
        )
        samples = self._repository.fetch_samples(chunk_id)
        proposals = self._repository.fetch_proposals(chunk_id)
        return ChunkDetail(
            **chunk,
            members=[
                MemberSummary(**member, label=_member_label(member))
                for member in members
            ],
            oem_samples=[
                self._sample_summary(sample, reused=True) for sample in samples
            ],
            proposals=[ProposalSummary(**proposal) for proposal in proposals],
        )

    def get_unresolved_overview(self, build_id: UUID) -> UnresolvedOverview:
        """Rank the source values NorthStar cannot yet interpret, by row count."""

        if self._repository.fetch_build(build_id) is None:
            raise MatchReviewNotFoundError(f"Unknown build {build_id}")
        populations: list[UnresolvedPopulation] = []
        for source_field, signature_field in SOURCE_TO_SIGNATURE.items():
            for entry in self._repository.fetch_unresolved_populations(
                build_id,
                source_field=source_field,
                signature_field=signature_field,
            ):
                populations.append(
                    UnresolvedPopulation(
                        source_field=source_field,
                        source_value=entry["source_value"],
                        signature_field=signature_field,
                        row_count=entry["row_count"],
                    )
                )
        populations.sort(key=lambda item: item.row_count, reverse=True)
        return UnresolvedOverview(build_id=build_id, populations=populations)

    def get_discriminators(
        self, build_id: UUID, *, source_field: str, source_value: str
    ) -> DiscriminatorReport:
        signature_field = SOURCE_TO_SIGNATURE.get(source_field)
        if signature_field is None:
            raise MatchReviewNotFoundError(
                f"{source_field} is not a resolvable source field."
            )
        population, breakdown = self._repository.fetch_discriminators(
            build_id,
            source_field=source_field,
            source_value=source_value,
            signature_field=signature_field,
            candidate_fields=CANDIDATE_DISCRIMINATORS,
        )
        fields = []
        for entry in breakdown:
            if entry["field"] == source_field:
                continue
            scored = score_discriminator(
                field=entry["field"],
                population=population,
                present_count=entry["present_count"],
                distinct_count=entry["distinct_count"],
                top_counts=[item["count"] for item in entry["top_values"]],
            )
            fields.append(
                DiscriminatorField(
                    field=scored.field,
                    distinct_count=scored.distinct_count,
                    present_count=scored.present_count,
                    coverage=scored.coverage,
                    separation=scored.separation,
                    concision=scored.concision,
                    score=scored.score,
                    usable=scored.usable,
                    top_values=_value_counts(entry["field"], entry["top_values"]),
                )
            )
        fields.sort(key=lambda item: item.score, reverse=True)
        return DiscriminatorReport(
            build_id=build_id,
            source_field=source_field,
            source_value=source_value,
            signature_field=signature_field,
            population=population,
            fields=fields,
        )

    def get_target_vocabulary(
        self, build_id: UUID, *, target_field: str
    ) -> TargetVocabulary:
        """Allowed or suggested values for a rule target.

        Closed vocabularies come from the reviewed rules; open ones fall back
        to values already observed in this build so free text can still be
        guided rather than guessed.
        """

        if target_field not in RESOLVABLE_TARGETS:
            raise MatchReviewNotFoundError(
                f"{target_field} is not an authorable target field."
            )
        canonical = RESOLVABLE_TARGETS[target_field]
        if canonical:
            return TargetVocabulary(
                target_field=target_field,
                closed=True,
                values=[
                    FieldValueCount(value=value, count=0) for value in canonical
                ],
                source="reviewed_rules",
            )
        observed = self._repository.fetch_signature_values(
            build_id, signature_field=target_field
        )
        return TargetVocabulary(
            target_field=target_field,
            closed=False,
            values=[
                FieldValueCount(value=row["value"], count=row["count"])
                for row in observed
            ],
            source="observed" if observed else "none",
        )

    def refine(self, request: RefineRequest) -> RefineResult:
        """Recompute counts and facets for the predicate as it stands.

        Every edit answers three questions at once: how many cars are matched,
        what still separates them, and whether anything identity-bearing still
        varies. The last one is the stopping condition — while `brand` or
        `model_no` still splits the matched rows, they are not one thing yet
        and assigning a single value would over-claim.
        """

        signature_field = self._signature_field_for(request.source_field)
        terms = [
            PredicateTerm(
                layer=condition.layer,
                field=condition.field,
                operator=condition.operator,
                values=condition.terms,
            )
            for condition in request.conditions
        ]
        counts = self._repository.preview_rule(
            request.build_id, conditions=terms, signature_field=signature_field
        )
        population, breakdown = self._repository.fetch_refined_discriminators(
            request.build_id,
            signature_field=signature_field,
            conditions=terms,
            candidate_fields=CANDIDATE_DISCRIMINATORS,
        )
        constrained = {condition.field for condition in request.conditions}
        fields = []
        for entry in breakdown:
            if entry["field"] in constrained:
                continue
            scored = score_discriminator(
                field=entry["field"],
                population=population,
                present_count=entry["present_count"],
                distinct_count=entry["distinct_count"],
                top_counts=[item["count"] for item in entry["top_values"]],
            )
            fields.append(
                DiscriminatorField(
                    field=scored.field,
                    distinct_count=scored.distinct_count,
                    present_count=scored.present_count,
                    coverage=scored.coverage,
                    separation=scored.separation,
                    concision=scored.concision,
                    score=scored.score,
                    usable=scored.usable,
                    top_values=_value_counts(entry["field"], entry["top_values"]),
                )
            )
        fields.sort(key=lambda item: item.score, reverse=True)

        # A field the reviewer has already constrained is not an open question:
        # grouping `MERCEDES-BENZ 204` with `204 K` is a deliberate statement
        # that those spellings mean the same car, so it must not keep blocking.
        varying = sorted(
            entry["field"]
            for entry in breakdown
            if entry["field"] in IDENTITY_FIELDS
            and entry["field"] not in constrained
            and entry["distinct_count"] > 1
        )
        trail_counts = self._repository.fetch_narrowing_trail(
            request.build_id, signature_field=signature_field, conditions=terms
        )
        trail = [
            NarrowingStep(
                label=_condition_label(condition),
                matched_rows=matched,
            )
            for condition, matched in zip(
                request.conditions, trail_counts, strict=False
            )
        ]
        return RefineResult(
            matched_rows=counts["matched_rows"],
            would_resolve=counts["would_resolve"],
            already_resolved=counts["already_resolved"],
            signature_field=signature_field,
            homogeneous=not varying and population > 0,
            varying_identity_fields=varying,
            trail=trail,
            fields=fields,
        )

    def preview_rule(self, request: RulePreviewRequest) -> RulePreview:
        """Dry-run a candidate rule. Nothing is written; this only counts."""

        if self._repository.fetch_build(request.build_id) is None:
            raise MatchReviewNotFoundError(f"Unknown build {request.build_id}")
        if request.target_field not in RESOLVABLE_TARGETS:
            raise MatchReviewConflictError(
                f"{request.target_field} is not an authorable target field."
            )
        allowed = RESOLVABLE_TARGETS[request.target_field]
        if allowed and request.target_value not in allowed:
            raise MatchReviewConflictError(
                f"{request.target_value} is not a canonical value for "
                f"{request.target_field}; expected one of {', '.join(allowed)}."
            )
        unknown = [
            condition.field
            for condition in request.conditions
            if (
                condition.layer == "normalized"
                and condition.field not in SIGNATURE_FIELDS
            )
            or (
                condition.layer == "source"
                and condition.field not in CANDIDATE_DISCRIMINATORS
                and condition.field not in SOURCE_TO_SIGNATURE
            )
        ]
        if unknown:
            raise MatchReviewConflictError(
                f"Unknown field(s) for the requested layer: "
                f"{', '.join(sorted(set(unknown)))}"
            )
        result = self._repository.preview_rule(
            request.build_id,
            conditions=[
                PredicateTerm(
                    layer=condition.layer,
                    field=condition.field,
                    operator=condition.operator,
                    values=condition.terms,
                )
                for condition in request.conditions
            ],
            signature_field=request.target_field,
        )
        return RulePreview(
            conditions=request.conditions,
            target_field=request.target_field,
            target_value=request.target_value,
            matched_rows=result["matched_rows"],
            would_resolve=result["would_resolve"],
            already_resolved=result["already_resolved"],
            sample_plates=result["sample_plates"],
        )

    def get_population_attributes(
        self, build_id: UUID, *, source_field: str, source_value: str
    ) -> PopulationAttributes:
        """Every source key in the population, not just the ranked candidates."""

        signature_field = self._signature_field_for(source_field)
        scanned, rows = self._repository.fetch_population_attributes(
            build_id,
            source_field=source_field,
            source_value=source_value,
            signature_field=signature_field,
        )
        population = max(
            (row["present_count"] for row in rows), default=scanned
        )
        population = max(population, scanned)
        return PopulationAttributes(
            build_id=build_id,
            source_field=source_field,
            source_value=source_value,
            population=population,
            scanned_members=scanned,
            sampled=scanned >= ATTRIBUTE_SCAN_LIMIT,
            attributes=[
                PopulationAttribute(
                    field=row["field"],
                    distinct_count=row["distinct_count"],
                    present_count=row["present_count"],
                    top_values=_value_counts(row["field"], row["top_values"]),
                )
                for row in rows
            ],
        )

    def get_value_patterns(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        field_name: str,
    ) -> PatternReport:
        """Shared prefixes that group many spellings into one condition."""

        population, values = self._field_values(
            build_id,
            source_field=source_field,
            source_value=source_value,
            field_name=field_name,
        )
        patterns = suggest_value_patterns(values, population=population)
        return PatternReport(
            field=field_name,
            population=population,
            patterns=[
                ValuePatternSuggestion(
                    prefix=pattern.prefix,
                    row_count=pattern.row_count,
                    distinct_values=pattern.distinct_values,
                    coverage=pattern.coverage,
                    score=pattern.score,
                )
                for pattern in patterns
            ],
        )

    def advise_rule(self, request: RuleAdviceRequest) -> RuleAdvice:
        """Propose a rule. Nothing is written; the result still needs preview."""

        if self._rule_advisor is None:
            raise MatchReviewConflictError("No rule advisor is configured.")
        report = self.get_discriminators(
            request.build_id,
            source_field=request.source_field,
            source_value=request.source_value,
        )
        # Value distributions for the strongest candidates, not just the top
        # one: prefix discovery needs the full spread to find things like the
        # chassis code inside `MERCEDES-BENZ 204 K`.
        usable_fields = [item.field for item in report.fields if item.usable]
        field_values: dict[str, list[tuple[str, int]]] = {}
        for field_name in usable_fields[:ADVISOR_VALUE_FIELDS]:
            _, values = self._field_values(
                request.build_id,
                source_field=request.source_field,
                source_value=request.source_value,
                field_name=field_name,
            )
            field_values[field_name] = values
        oem_samples = [
            _sanitize_payload(sample)
            for sample in self._repository.fetch_population_oem_samples(
                request.build_id,
                source_field=request.source_field,
                source_value=request.source_value,
                signature_field=report.signature_field,
            )
        ]
        advice = self._rule_advisor.advise(
            source_field=request.source_field,
            source_value=request.source_value,
            target_field=report.signature_field,
            population=report.population,
            discriminators=[item.model_dump() for item in report.fields],
            field_values=field_values,
            oem_samples=oem_samples,
        )
        return RuleAdvice(
            advisor=advice.advisor,
            confident=advice.confident,
            conditions=[
                RuleCondition(
                    field=condition.field,
                    values=list(condition.values),
                    layer=condition.layer,  # type: ignore[arg-type]
                    operator=condition.operator,  # type: ignore[arg-type]
                )
                for condition in advice.conditions
            ],
            target_field=advice.target_field,
            target_value=advice.target_value,
            reasoning=advice.reasoning,
            evidence=advice.evidence,
        )

    def _signature_field_for(self, source_field: str) -> str:
        signature_field = SOURCE_TO_SIGNATURE.get(source_field)
        if signature_field is None:
            raise MatchReviewNotFoundError(
                f"{source_field} is not a resolvable source field."
            )
        return signature_field

    def _field_values(
        self,
        build_id: UUID,
        *,
        source_field: str,
        source_value: str,
        field_name: str,
    ) -> tuple[int, list[tuple[str, int]]]:
        signature_field = self._signature_field_for(source_field)
        population, breakdown = self._repository.fetch_discriminators(
            build_id,
            source_field=source_field,
            source_value=source_value,
            signature_field=signature_field,
            candidate_fields=(field_name,),
            top_values=200,
        )
        values = [
            (item["value"], item["count"])
            for entry in breakdown
            if entry["field"] == field_name
            for item in entry["top_values"]
        ]
        return population, values

    def get_field_profile(self, chunk_id: UUID) -> ChunkFieldProfile:
        """Show which source fields are uniform across the chunk and which are not.

        Uniformity is what licenses extrapolating one vehicle's decision to
        every member; a varying identity field means the signature grouped
        materially different vehicles.
        """

        chunk = self._require_chunk(chunk_id)
        scanned, profile = self._repository.fetch_field_profile(
            chunk_id,
            fields=PROFILED_SOURCE_FIELDS,
            sample_limit=FIELD_PROFILE_SCAN_LIMIT,
        )
        fields = [
            FieldVariance(
                field=entry["field"],
                distinct_count=entry["distinct_count"],
                present_count=entry["present_count"],
                uniform=entry["distinct_count"] <= 1,
                top_values=_value_counts(entry["field"], entry["top_values"]),
            )
            for entry in profile
        ]
        return ChunkFieldProfile(
            chunk_id=chunk_id,
            member_count=chunk["member_count"],
            scanned_members=scanned,
            truncated=chunk["member_count"] > scanned,
            varying_fields=[
                field.field for field in fields if not field.uniform
            ],
            fields=fields,
        )

    def get_member_comparison(
        self, chunk_id: UUID, source_record_id: int
    ) -> MemberComparison:
        self._require_chunk(chunk_id)
        evidence = self._repository.fetch_member_evidence(
            chunk_id, source_record_id
        )
        if evidence is None:
            raise MatchReviewNotFoundError(
                f"Record {source_record_id} is not a member of this chunk."
            )
        source = evidence["source_record"]
        normalized = evidence["normalized_payload"].get("normalized")
        candidates = evidence["normalized_payload"].get("candidates")
        oem_payload = next(
            (
                sample["response_payload"]
                for sample in self._repository.fetch_samples(chunk_id)
                if sample["source_record_id"] == source_record_id
            ),
            None,
        )
        rows = []
        for label, source_keys, normalized_keys, oem_keys in _COMPARISON_FIELDS:
            normalized_value = _first(normalized, *normalized_keys) or _first(
                candidates, *normalized_keys
            )
            oem_value = _first(oem_payload, *oem_keys) if oem_payload else None
            conflict: bool | None = None
            if oem_value is not None and normalized_value is not None:
                conflict = oem_value.casefold() != normalized_value.casefold()
            source_value, source_key = _first_with_key(source, *source_keys)
            rows.append(
                ComparisonRow(
                    field=label,
                    source_field=source_key,
                    resolvable=source_key in SOURCE_TO_SIGNATURE,
                    status=field_status(source_value, normalized_value).value,
                    source_value=source_value,
                    normalized_value=normalized_value,
                    oem_value=oem_value,
                    conflict=conflict,
                )
            )
        plate = _first(source, "plate")
        member = {
            "source_record_id": source_record_id,
            "plate": plate,
            "source_manufacturer": _first(source, "manufacturer", "brand"),
            "source_model": _first(source, "model"),
            "source_year": _first(source, "vehicle_year"),
        }
        return MemberComparison(
            source_record_id=source_record_id,
            label=_member_label(member),
            plate=plate,
            has_oem_evidence=oem_payload is not None,
            rows=rows,
        )

    def fetch_oem_sample(
        self, chunk_id: UUID, request: OemSampleRequest
    ) -> OemSampleSummary:
        self._require_chunk(chunk_id)
        vin = self._repository.fetch_member_vin(
            chunk_id, request.source_record_id
        )
        if vin is None:
            raise MemberVinUnavailableError(
                f"Member {request.source_record_id} is not in this chunk or "
                "has no VIN on its raw record."
            )
        provider = self._oem_provider.provider_name
        dataset_version = self._oem_provider.dataset_version
        cached = self._repository.fetch_oem_evidence(
            provider=provider, vin=vin, dataset_version=dataset_version
        )
        reused = cached is not None
        if cached is None:
            payload = self._oem_provider.fetch_vehicle(vin)
            cached = self._repository.insert_oem_evidence(
                request_id=request.request_id,
                provider=provider,
                vin=vin,
                dataset_version=dataset_version,
                response_payload=payload,
            )
        self._repository.link_sample(
            chunk_id=chunk_id,
            evidence_id=cached["id"],
            source_record_id=request.source_record_id,
        )
        sample = {
            "sample_id": cached["id"],
            "source_record_id": request.source_record_id,
            "provider": provider,
            "vin": vin,
            "dataset_version": dataset_version,
            "response_payload": cached["response_payload"],
            "fetched_at": cached["fetched_at"],
        }
        return self._sample_summary(sample, reused=reused)

    def create_proposal(self, chunk_id: UUID) -> ProposalSummary:
        chunk = self._require_chunk(chunk_id)
        if chunk["status"] not in {"open", "proposed"}:
            raise MatchReviewConflictError(
                f"Chunk is already {chunk['status']}; new proposals are closed."
            )
        samples = self._repository.fetch_samples(chunk_id)
        profile = self.get_field_profile(chunk_id)
        proposal = self._adjudicator.adjudicate(
            signature=chunk["signature"],
            reason_profile=chunk["reason_profile"],
            member_count=chunk["member_count"],
            oem_samples=[sample["response_payload"] for sample in samples],
            tecdoc_candidates=[],
            varying_fields=profile.varying_fields,
        )
        stored = self._repository.insert_proposal(
            proposal_id=uuid4(),
            chunk_id=chunk_id,
            proposal_source=self._proposal_source,
            adjudicator_version=self._adjudicator.version,
            recommendation=proposal.recommendation,
            target_ktype_reference=proposal.target_ktype_reference,
            confidence=proposal.confidence,
            evidence=proposal.evidence,
            reasoning=proposal.reasoning,
        )
        return ProposalSummary(**stored)

    def review_proposal(
        self, proposal_id: UUID, request: ProposalReviewRequest
    ) -> ProposalSummary:
        proposal = self._repository.fetch_proposal(proposal_id)
        if proposal is None:
            raise MatchReviewNotFoundError(f"Unknown proposal {proposal_id}")
        if proposal["status"] != "proposed":
            raise MatchReviewConflictError(
                f"Proposal was already {proposal['status']}."
            )
        if request.action == "approve":
            status = "approved"
            chunk_status = (
                "split"
                if proposal["recommendation"] == "split_chunk"
                else "approved"
            )
        else:
            status = "rejected"
            chunk_status = "open"
        reviewed = self._repository.review_proposal(
            proposal_id=proposal_id,
            status=status,
            chunk_status=chunk_status,
            reviewer=request.reviewer,
            note=request.note,
        )
        if reviewed is None:
            raise MatchReviewConflictError(
                "Proposal was reviewed concurrently; reload the chunk."
            )
        return ProposalSummary(**reviewed)

    def _require_chunk(self, chunk_id: UUID) -> dict[str, Any]:
        chunk = self._repository.fetch_chunk(chunk_id)
        if chunk is None:
            raise MatchReviewNotFoundError(f"Unknown chunk {chunk_id}")
        return chunk

    def _sample_summary(
        self, sample: dict[str, Any], *, reused: bool
    ) -> OemSampleSummary:
        return OemSampleSummary(
            sample_id=sample["sample_id"],
            source_record_id=sample["source_record_id"],
            provider=sample["provider"],
            masked_vin=mask_vin(sample["vin"]),
            dataset_version=sample["dataset_version"],
            fetched_at=sample["fetched_at"],
            reused_cached_evidence=reused,
            response_payload=_sanitize_payload(sample["response_payload"]),
        )
