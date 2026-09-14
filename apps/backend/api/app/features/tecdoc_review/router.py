from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from api.app.core.db import get_postgres_connection
from api.app.core.settings import Settings, get_settings
from api.app.features.match_review.chunk_router import (
    get_match_review_service,
    get_rule_application_runner,
)
from api.app.features.match_review.chunk_service import (
    MatchReviewConflictError,
    MatchReviewNotFoundError,
)
from api.app.features.match_review.rule_application import RuleAlreadyRunningError
from api.app.features.tecdoc_review.gaps import TecDocResolveError
from api.app.features.tecdoc_review.predicate import UnknownTecDocFieldError
from api.app.features.tecdoc_review.reimport import (
    TecDocReimportAlreadyRunningError,
    TecDocReimportNotConfiguredError,
    TecDocReimportRunner,
)
from api.app.features.tecdoc_review.repository import TecDocReviewRepository
from api.app.features.tecdoc_review.rules_export import (
    NoCompletedBuildError,
    RemoteSyncError,
    RulesBundleService,
)
from api.app.features.tecdoc_review.schemas import (
    RulesBundleExport,
    RulesBundleImportRequest,
    RulesBundleImportResult,
    RulesSyncRequest,
    TecDocEntityPage,
    TecDocGapValuesResponse,
    TecDocReimportStatus,
    TecDocResolution,
    TecDocResolveRequest,
    TecDocReviewPage,
    TecDocUnresolvedSummary,
    TecDocVehicleCount,
    TecDocVehicleDetail,
    TecDocVehicleFacet,
    TecDocVehicleFilter,
)
from api.app.features.tecdoc_review.service import TecDocReviewService

router = APIRouter(prefix="/v1/normalization-review/tecdoc", tags=["tecdoc-review"])


@lru_cache(maxsize=1)
def _cached_reimport_runner() -> TecDocReimportRunner:
    return TecDocReimportRunner()


def get_tecdoc_reimport_runner() -> TecDocReimportRunner:
    return _cached_reimport_runner()


@lru_cache(maxsize=1)
def _cached_rules_bundle_service() -> RulesBundleService:
    return RulesBundleService()


def get_rules_bundle_service() -> RulesBundleService:
    return _cached_rules_bundle_service()


def require_rules_sync_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_rules_sync_token: Annotated[str | None, Header()] = None,
) -> None:
    """Plain shared-secret compare, not real auth.

    Unset RULES_SYNC_TOKEN on this server and these endpoints stay exactly as
    open as they always were -- a placeholder until something real replaces
    it, not a security boundary in its own right.
    """

    if not settings.rules_sync_token:
        return
    if x_rules_sync_token != settings.rules_sync_token:
        raise HTTPException(status_code=401, detail="Invalid or missing sync token.")


def _basic_auth(request: RulesSyncRequest) -> tuple[str, str] | None:
    """`live_base_url` may sit behind nginx `auth_basic` (production does --
    see infra/production/nginx.conf), which rejects the request before the
    app-level sync token is ever checked. Distinct credential, only sent when
    both halves are given."""

    if request.basic_auth_user and request.basic_auth_password:
        return (request.basic_auth_user, request.basic_auth_password)
    return None


def get_tecdoc_review_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TecDocReviewService:
    return TecDocReviewService(TecDocReviewRepository(lambda: get_postgres_connection(settings)))


@router.get("/vehicles", response_model=TecDocReviewPage)
def list_tecdoc_vehicles(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocReviewPage:
    try:
        return service.list_vehicles(query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="TecDoc review data is temporarily unavailable."
        ) from error


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503, detail="TecDoc review data is temporarily unavailable."
    )


def _bad_field(error: UnknownTecDocFieldError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.post("/vehicles/page", response_model=TecDocReviewPage)
def page_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocReviewPage:
    """Filtered, paged KTypes -- the structured-condition sibling of `/vehicles`."""

    try:
        return service.list_vehicles(
            query=request.query,
            limit=limit,
            offset=offset,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/count", response_model=TecDocVehicleCount)
def count_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocVehicleCount:
    try:
        return service.count_vehicles(
            query=request.query,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/unresolved-summary", response_model=TecDocUnresolvedSummary)
def tecdoc_unresolved_summary(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocUnresolvedSummary:
    """What the filtered KTypes still cannot say about themselves -- the worklist."""

    try:
        return service.unresolved_vehicle_summary(
            query=request.query, conditions=request.conditions
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/vehicles/facets", response_model=TecDocVehicleFacet)
def facet_tecdoc_vehicles(
    request: TecDocVehicleFilter,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    field: str = Query(max_length=60),
    limit: int = Query(default=12, ge=1, le=100),
) -> TecDocVehicleFacet:
    """Top values of one field inside the filter -- what still varies."""

    try:
        return service.facet_vehicles(
            query=request.query,
            conditions=request.conditions,
            unresolved_field=request.unresolved_field,
            field=field,
            limit=limit,
        )
    except UnknownTecDocFieldError as error:
        raise _bad_field(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/vehicles/detail", response_model=TecDocVehicleDetail)
def tecdoc_vehicle_detail(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    source_key: str = Query(...),
) -> TecDocVehicleDetail:
    """One KType's canonical fields, each with its outcome -- opened from a row
    the same way TS's record panel opens from a car, so the fields still
    missing a value are the ones with a Resolve action next to them."""

    detail = service.vehicle_detail(source_key=source_key)
    if detail is None:
        raise HTTPException(status_code=404, detail="No promoted KType with that source key.")
    return detail


def _bad_resolve(error: TecDocResolveError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


@router.get("/gaps", response_model=TecDocGapValuesResponse)
def tecdoc_gap_values(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    field: Literal[
        "energy_sources", "bodywork_form", "drive_type", "transmission_type"
    ] = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
) -> TecDocGapValuesResponse:
    """The distinct raw values behind one canonical field's gap -- the click
    target a reviewer resolves, one level under the row-level ktype count
    `/vehicles/unresolved-summary` reports."""

    try:
        return service.gap_values(canonical_field=field, limit=limit)
    except TecDocResolveError as error:
        raise _bad_resolve(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.post("/gaps/resolve", response_model=TecDocResolution)
def resolve_tecdoc_gap_value(
    request: TecDocResolveRequest,
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
) -> TecDocResolution:
    """Write one reviewer's live ruling on one value.

    Not the same act as sealing a `tecdoc_rules` version: this is immediately
    visible in `/gaps` and `/vehicles/*`, mutable, and separate from the
    reviewed mappings `generate-tecdoc-rules` reads from
    `ingestion.tecdoc.reference_data` -- promoting a ruling made here into that
    file is a deliberate follow-up, not something this endpoint does itself.
    """

    try:
        return service.resolve(
            canonical_field=request.canonical_field,
            source_term=request.source_term,
            decision=request.decision,
            canonical_value=request.canonical_value,
            note=request.note,
            reviewed_by=request.reviewed_by,
            source_system=request.source_system,
            relation=request.relation,
            support=request.support,
        )
    except TecDocResolveError as error:
        raise _bad_resolve(error) from error
    except psycopg.Error as error:
        raise _unavailable() from error


@router.get("/entities", response_model=TecDocEntityPage)
def list_tecdoc_entities(
    service: Annotated[TecDocReviewService, Depends(get_tecdoc_review_service)],
    kind: Literal[
        "manufacturer", "model_family", "engine", "fuel", "bodywork", "transmission", "drive"
    ] = "manufacturer",
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
) -> TecDocEntityPage:
    try:
        return service.list_entities(kind=kind, query=query, limit=limit, offset=offset)
    except psycopg.Error as error:
        raise HTTPException(
            status_code=503, detail="TecDoc entity data is temporarily unavailable."
        ) from error


@router.post("/reimport", response_model=TecDocReimportStatus, status_code=202)
def start_tecdoc_reimport(
    background: BackgroundTasks,
    runner: Annotated[TecDocReimportRunner, Depends(get_tecdoc_reimport_runner)],
) -> TecDocReimportStatus:
    """Start a full reimport: fresh `.dat` extraction, written to Postgres and the graph.

    Returns as soon as the run is claimed, not when it finishes -- the full
    drop takes several minutes. Poll `GET /reimport/latest` until it settles.
    """

    try:
        run = runner.start()
    except TecDocReimportNotConfiguredError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TecDocReimportAlreadyRunningError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
    background.add_task(runner.run, job_id=run.job_id, batch_id=run.batch_id)
    return TecDocReimportStatus(
        job_id=run.job_id,
        batch_id=run.batch_id,
        status=run.status,
        source_ktypes=run.source_ktypes,
        graph_rows_written=run.graph_rows_written,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_summary=run.error_summary,
    )


@router.get("/reimport/latest", response_model=TecDocReimportStatus)
def get_latest_tecdoc_reimport(
    runner: Annotated[TecDocReimportRunner, Depends(get_tecdoc_reimport_runner)],
) -> TecDocReimportStatus:
    """The latest reimport run -- what the screen polls while it works."""

    try:
        run = runner.latest()
    except psycopg.Error as error:
        raise _unavailable() from error
    if run is None:
        raise HTTPException(status_code=404, detail="No reimport has been run yet.")
    return TecDocReimportStatus(
        job_id=run.job_id,
        batch_id=run.batch_id,
        status=run.status,
        source_ktypes=run.source_ktypes,
        graph_rows_written=run.graph_rows_written,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_summary=run.error_summary,
    )


@router.get(
    "/resolution-rules/export",
    response_model=RulesBundleExport,
    dependencies=[Depends(require_rules_sync_token)],
)
def export_resolution_rules(
    service: Annotated[RulesBundleService, Depends(get_rules_bundle_service)],
) -> RulesBundleExport:
    """Every manually-authored rule (TecDoc + TS) in this database, as one file.

    The DB stays the source of truth; this is just a carrier to another one.
    """

    try:
        bundle = service.export()
    except psycopg.Error as error:
        raise _unavailable() from error
    return RulesBundleExport(
        exported_at=bundle.exported_at,
        tecdoc_rules=bundle.tecdoc_rules,
        ts_rules=bundle.ts_rules,
        policy_versions=bundle.policy_versions,
        tecdoc_rule_versions=bundle.tecdoc_rule_versions,
        tecdoc_rule_catalog=bundle.tecdoc_rule_catalog,
    )


def _queue_ts_rule_applications(
    background: BackgroundTasks, rule_ids: tuple[str, ...]
) -> list[str]:
    """Schedule each newly-imported TS rule's own apply job.

    Reuses exactly the plan/start/run sequence `POST /match-review/
    resolution-rules/{rule_id}/apply` runs for a rule saved by hand: the
    predicate a rule was authored with only ever touches the rows it matches,
    so importing ten rules queues ten narrow jobs, never a population-wide
    reprocess. A rule that fails to plan or is already running is skipped
    rather than failing the whole import -- the rule itself is still saved;
    only its re-run needs a retry (e.g. via the /rules screen's own Apply).
    """

    service = get_match_review_service()
    runner = get_rule_application_runner()
    queued: list[str] = []
    for raw_id in rule_ids:
        rule_id = UUID(raw_id)
        try:
            plan = service.plan_resolution_rule_application(rule_id)
            application = runner.start(rule_id)
        except (MatchReviewNotFoundError, MatchReviewConflictError, RuleAlreadyRunningError):
            continue

        def on_finish(rows: int, rid: UUID = rule_id) -> None:
            # Bound per iteration: the default freezes this loop's rule_id.
            service.record_rule_applied(rid, rows_written=rows, applied_by="rules-bundle-import")

        background.add_task(
            runner.run,
            job_id=application.job_id,
            rule_id=rule_id,
            build_id=plan.build_id,
            predicate=plan.predicate,
            target_field=plan.target_field,
            target_value=plan.target_value,
            applied_by="rules-bundle-import",
            on_finish=on_finish,
        )
        queued.append(raw_id)
    return queued


@router.post(
    "/resolution-rules/import",
    response_model=RulesBundleImportResult,
    dependencies=[Depends(require_rules_sync_token)],
)
def import_resolution_rules(
    request: RulesBundleImportRequest,
    background: BackgroundTasks,
    service: Annotated[RulesBundleService, Depends(get_rules_bundle_service)],
) -> RulesBundleImportResult:
    """Apply a bundle from `GET /resolution-rules/export` into this database.

    Always writes (no dry run here -- the CLI scripts this wraps keep that
    for hand-inspection; a reviewer clicking Import already means to commit).
    Every newly-inserted TS rule is then queued to apply itself against just
    the rows its own condition matches -- see `_queue_ts_rule_applications`.
    """

    try:
        result = service.import_bundle(
            tecdoc_rules=request.tecdoc_rules,
            ts_rules=request.ts_rules,
            policy_versions=request.policy_versions,
            tecdoc_rule_versions=request.tecdoc_rule_versions,
            tecdoc_rule_catalog=request.tecdoc_rule_catalog,
        )
    except NoCompletedBuildError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
    queued = _queue_ts_rule_applications(background, result.ts_created_rule_ids)
    return RulesBundleImportResult(
        tecdoc_single_target=result.tecdoc_single_target,
        tecdoc_compatible=result.tecdoc_compatible,
        ts_created=result.ts_created,
        ts_already_present=result.ts_already_present,
        ts_skipped_invalid=result.ts_skipped_invalid,
        ts_target_build=result.ts_target_build,
        ts_rules_queued_for_apply=queued,
        tecdoc_conflicts=list(result.tecdoc_conflicts),
        policy_versions_created=result.policy_versions_created,
        policy_versions_already_present=result.policy_versions_already_present,
        tecdoc_rule_versions_created=result.tecdoc_rule_versions_created,
        tecdoc_rule_versions_already_present=result.tecdoc_rule_versions_already_present,
    )


@router.post("/resolution-rules/sync/pull", response_model=RulesBundleImportResult)
def pull_resolution_rules(
    request: RulesSyncRequest,
    background: BackgroundTasks,
    service: Annotated[RulesBundleService, Depends(get_rules_bundle_service)],
) -> RulesBundleImportResult:
    """Fetch `live_base_url`'s own rules bundle and import it here.

    Server-to-server: this process calls the other one's `GET
    /resolution-rules/export` directly, no browser download/upload step and
    no CORS to configure. The conflict check runs here, against this
    database's own rows, exactly as a local file import would.
    """

    try:
        result = service.pull_from(
            request.live_base_url,
            token=request.token,
            basic_auth=_basic_auth(request),
        )
    except (RemoteSyncError, NoCompletedBuildError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
    queued = _queue_ts_rule_applications(background, result.ts_created_rule_ids)
    return RulesBundleImportResult(
        tecdoc_single_target=result.tecdoc_single_target,
        tecdoc_compatible=result.tecdoc_compatible,
        ts_created=result.ts_created,
        ts_already_present=result.ts_already_present,
        ts_skipped_invalid=result.ts_skipped_invalid,
        ts_target_build=result.ts_target_build,
        ts_rules_queued_for_apply=queued,
        tecdoc_conflicts=list(result.tecdoc_conflicts),
        ts_skipped_no_build=result.ts_skipped_no_build,
        policy_versions_created=result.policy_versions_created,
        policy_versions_already_present=result.policy_versions_already_present,
        tecdoc_rule_versions_created=result.tecdoc_rule_versions_created,
        tecdoc_rule_versions_already_present=result.tecdoc_rule_versions_already_present,
    )


@router.post("/resolution-rules/sync/push", response_model=RulesBundleImportResult)
def push_resolution_rules(
    request: RulesSyncRequest,
    service: Annotated[RulesBundleService, Depends(get_rules_bundle_service)],
) -> RulesBundleImportResult:
    """Export this database's bundle and hand it to `live_base_url`'s own
    import endpoint. The conflict check runs *there*, against *its* data --
    this call only reports back whatever that server decided."""

    try:
        result = service.push_to(
            request.live_base_url,
            token=request.token,
            basic_auth=_basic_auth(request),
        )
    except RemoteSyncError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except psycopg.Error as error:
        raise _unavailable() from error
    return RulesBundleImportResult(
        tecdoc_single_target=result.tecdoc_single_target,
        tecdoc_compatible=result.tecdoc_compatible,
        ts_created=result.ts_created,
        ts_already_present=result.ts_already_present,
        ts_skipped_invalid=result.ts_skipped_invalid,
        ts_target_build=result.ts_target_build,
        ts_rules_queued_for_apply=list(result.ts_created_rule_ids),
        tecdoc_conflicts=list(result.tecdoc_conflicts),
        policy_versions_created=result.policy_versions_created,
        policy_versions_already_present=result.policy_versions_already_present,
        tecdoc_rule_versions_created=result.tecdoc_rule_versions_created,
        tecdoc_rule_versions_already_present=result.tecdoc_rule_versions_already_present,
    )
