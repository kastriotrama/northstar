import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { API_BASE_URL } from './api-config';
import type {
  CorrectionDecisionList,
  CorrectionDecisionRequest,
  CorrectionDecisionResult,
  CorrectionOutcome,
  CorrectionPreviewCars,
  CorrectionPreviewJob,
  CorrectionPreviewRequest,
  CorrectionScopes,
  CorrectionScopesRequest,
  CorrectionWithdrawRequest,
  CorrectionWithdrawal,
  FactCorrectionHistory,
  FactCorrectionRequest,
  KTypeChoiceHistory,
  KTypeChoiceRequest,
  CoverageBatch,
  DiscriminatorReport,
  MatchChunkBuild,
  MatchReviewPatternPage,
  MatchRunSummary,
  PatternReport,
  PopulationAttributes,
  RefineResult,
  ResolutionRule,
  ReviewerRuleChange,
  ResolutionRuleApplication,
  RuleAdvice,
  RuleCatalogResponse,
  RuleCondition,
  RulePreview,
  SourceBatch,
  SourceFieldInventory,
  SourceRecordDetail,
  SourceRecordPage,
  TargetVocabulary,
  TecDocCoverageReport,
  TecDocEntityPage,
  RulesBundleImportResult,
  TecDocGapValuesResponse,
  TecDocPage,
  TecDocReimportStatus,
  TecDocResolution,
  TecDocResolveRequest,
  TecDocUnresolvedSummary,
  TecDocVehicleCount,
  TecDocVehicleDetail,
  TecDocVehicleFacet,
  TecDocVehicleFilter,
  TsCoverageReport,
  UnresolvedOverview,
  UnresolvedSummary,
  MatchResultCounts,
  MatchResultOverview,
  VehicleMatchLookup,
  VehicleCondition,
  VehicleCount,
  VehicleDetail,
  VehicleFacet,
  VehicleFilterRequest,
  VehiclePage,
  GapGroupingMode,
  GapGroupReport,
  NorVehicleFacet,
  NorVehiclePage,
  NorVehicleRecord,
  VehicleFieldInfo,
  VehicleSearchRequest,
} from './models';

/** Drops null/undefined/empty values so optional filters stay out of the query string. */
function params(source: Record<string, string | number | null | undefined>): HttpParams {
  let result = new HttpParams();
  for (const [key, value] of Object.entries(source)) {
    if (value === null || value === undefined || value === '') {
      continue;
    }
    result = result.set(key, String(value));
  }
  return result;
}

/** Attaches the rules-bundle sync token, if any -- checked only when the
 * receiving server's own RULES_SYNC_TOKEN is set; a plain shared-secret
 * compare, not real auth. */
function syncTokenHeader(token: string | undefined): Record<string, string> {
  return token ? { 'X-Rules-Sync-Token': token } : {};
}

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  // --- Page 1: raw Transportstyrelsen staging rows -------------------------------------
  // Paging is keyset-based: pass the previous page's `next_cursor`, never an offset.
  listSourceRecords(options: {
    query?: string;
    field?: string | null;
    value?: string | null;
    batchId?: string | null;
    cursor?: number | null;
    limit?: number;
  }): Observable<SourceRecordPage> {
    return this.http.get<SourceRecordPage>(`${this.base}/v1/source-records/ts`, {
      params: params({
        query: options.query,
        field: options.field,
        value: options.value,
        batch_id: options.batchId,
        cursor: options.cursor,
        limit: options.limit ?? 100,
      }),
    });
  }

  getSourceRecord(recordId: number): Observable<SourceRecordDetail> {
    return this.http.get<SourceRecordDetail>(`${this.base}/v1/source-records/ts/${recordId}`);
  }

  listSourceBatches(): Observable<{ items: SourceBatch[] }> {
    return this.http.get<{ items: SourceBatch[] }>(`${this.base}/v1/source-records/ts/batches`);
  }

  sourceFieldInventory(): Observable<SourceFieldInventory> {
    return this.http.get<SourceFieldInventory>(`${this.base}/v1/source-records/ts/fields`);
  }

  // --- Page 2: TecDoc ------------------------------------------------------------------
  listTecDocVehicles(options: {
    query?: string;
    limit?: number;
    offset?: number;
  }): Observable<TecDocPage> {
    return this.http.get<TecDocPage>(`${this.base}/v1/normalization-review/tecdoc/vehicles`, {
      params: params({
        query: options.query,
        limit: options.limit ?? 100,
        offset: options.offset ?? 0,
      }),
    });
  }

  listTecDocEntities(options: {
    kind: string;
    query?: string;
    limit?: number;
    offset?: number;
  }): Observable<TecDocEntityPage> {
    return this.http.get<TecDocEntityPage>(`${this.base}/v1/normalization-review/tecdoc/entities`, {
      params: params({
        kind: options.kind,
        query: options.query,
        limit: options.limit ?? 100,
        offset: options.offset ?? 0,
      }),
    });
  }

  // --- TecDoc vehicles: filtered, ported from `/v1/ts-records` --------------------------

  tecdocVehiclePage(
    filter: TecDocVehicleFilter,
    options: { limit?: number; offset?: number } = {},
  ): Observable<TecDocPage> {
    return this.http.post<TecDocPage>(
      `${this.base}/v1/normalization-review/tecdoc/vehicles/page`,
      filter,
      { params: params({ limit: options.limit ?? 100, offset: options.offset ?? 0 }) },
    );
  }

  countTecDocVehicles(filter: TecDocVehicleFilter): Observable<TecDocVehicleCount> {
    return this.http.post<TecDocVehicleCount>(
      `${this.base}/v1/normalization-review/tecdoc/vehicles/count`,
      filter,
    );
  }

  /** What the filtered KTypes still cannot say about themselves -- the worklist. */
  tecdocUnresolvedSummary(filter: TecDocVehicleFilter): Observable<TecDocUnresolvedSummary> {
    return this.http.post<TecDocUnresolvedSummary>(
      `${this.base}/v1/normalization-review/tecdoc/vehicles/unresolved-summary`,
      filter,
    );
  }

  tecdocVehicleFacet(
    filter: TecDocVehicleFilter,
    field: string,
    limit = 12,
  ): Observable<TecDocVehicleFacet> {
    return this.http.post<TecDocVehicleFacet>(
      `${this.base}/v1/normalization-review/tecdoc/vehicles/facets`,
      filter,
      { params: params({ field, limit }) },
    );
  }

  /** The distinct raw values behind one canonical field's gap -- the Resolve click target. */
  tecdocGapValues(field: string, limit = 100): Observable<TecDocGapValuesResponse> {
    return this.http.get<TecDocGapValuesResponse>(
      `${this.base}/v1/normalization-review/tecdoc/gaps`,
      { params: params({ field, limit }) },
    );
  }

  /** Write one reviewer's live ruling on one TecDoc value, or one cross-system
   * synonym rule (fuel/bodywork/drive) -- same endpoint, same table. */
  resolveTecDocGap(request: TecDocResolveRequest): Observable<TecDocResolution> {
    return this.http.post<TecDocResolution>(
      `${this.base}/v1/normalization-review/tecdoc/gaps/resolve`,
      request,
    );
  }

  /** One KType's canonical fields, each with its outcome -- opened from a row
   * the same way TS's record panel opens from a car. */
  tecdocVehicleDetail(sourceKey: string): Observable<TecDocVehicleDetail> {
    return this.http.get<TecDocVehicleDetail>(
      `${this.base}/v1/normalization-review/tecdoc/vehicles/detail`,
      { params: params({ source_key: sourceKey }) },
    );
  }

  /**
   * Starts a full TecDoc reimport: fresh `.dat` extraction, written to Postgres
   * and the live graph. Returns as soon as the run is claimed, not when it
   * finishes -- the full drop takes several minutes. Poll `tecdocReimportStatus`
   * until it settles.
   */
  startTecDocReimport(): Observable<TecDocReimportStatus> {
    return this.http.post<TecDocReimportStatus>(
      `${this.base}/v1/normalization-review/tecdoc/reimport`,
      {},
    );
  }

  tecdocReimportStatus(): Observable<TecDocReimportStatus> {
    return this.http.get<TecDocReimportStatus>(
      `${this.base}/v1/normalization-review/tecdoc/reimport/latest`,
    );
  }

  /** Fetches `liveBaseUrl`'s own rules bundle (server to server, no CORS) and
   * imports it here. A row this DB edited more recently is left untouched --
   * see `tecdoc_conflicts` on the result. The same shared token is both sent
   * as this call's own header (checked only if this server sets one) and
   * forwarded in the body for the other server to check on its own export. */
  pullResolutionRules(
    liveBaseUrl: string,
    token?: string,
    basicAuth?: { user: string; password: string },
  ): Observable<RulesBundleImportResult> {
    return this.http.post<RulesBundleImportResult>(
      `${this.base}/v1/normalization-review/tecdoc/resolution-rules/sync/pull`,
      {
        live_base_url: liveBaseUrl,
        token: token || null,
        basic_auth_user: basicAuth?.user || null,
        basic_auth_password: basicAuth?.password || null,
      },
      { headers: syncTokenHeader(token) },
    );
  }

  /** Exports this DB's bundle and hands it to `liveBaseUrl`'s own import
   * endpoint. The conflict check runs there, against its data. */
  pushResolutionRules(
    liveBaseUrl: string,
    token?: string,
    basicAuth?: { user: string; password: string },
  ): Observable<RulesBundleImportResult> {
    return this.http.post<RulesBundleImportResult>(
      `${this.base}/v1/normalization-review/tecdoc/resolution-rules/sync/push`,
      {
        live_base_url: liveBaseUrl,
        token: token || null,
        basic_auth_user: basicAuth?.user || null,
        basic_auth_password: basicAuth?.password || null,
      },
      { headers: syncTokenHeader(token) },
    );
  }

  // --- Page 3: rules -------------------------------------------------------------------
  // Uses the paginated catalog, not GET /rules: that endpoint returns ~12MB in ~25s
  // because it also aggregates the newest normalization batch.
  listRules(options: {
    query?: string;
    area?: string | null;
    canonicalField?: string | null;
    decision?: string | null;
    origin?: string | null;
    source?: string | null;
    includeInventory?: boolean;
    transformerId?: string | null;
    limit?: number;
    offset?: number;
  }): Observable<RuleCatalogResponse> {
    return this.http.get<RuleCatalogResponse>(
      `${this.base}/v1/normalization-review/rules/catalog`,
      {
        params: params({
          query: options.query,
          area: options.area,
          canonical_field: options.canonicalField,
          decision: options.decision,
          origin: options.origin,
          source: options.source,
          include_inventory: options.includeInventory ? 'true' : undefined,
          transformer_id: options.transformerId,
          limit: options.limit ?? 100,
          offset: options.offset ?? 0,
        }),
      },
    );
  }

  // --- Page 4: coverage ----------------------------------------------------------------
  listCoverageBatches(): Observable<{ items: CoverageBatch[] }> {
    return this.http.get<{ items: CoverageBatch[] }>(`${this.base}/v1/coverage/batches`);
  }

  tsCoverage(batchId?: string | null): Observable<TsCoverageReport> {
    return this.http.get<TsCoverageReport>(`${this.base}/v1/coverage/ts`, {
      params: params({ batch_id: batchId }),
    });
  }

  tecdocCoverage(): Observable<TecDocCoverageReport> {
    return this.http.get<TecDocCoverageReport>(`${this.base}/v1/coverage/tecdoc`);
  }

  // --- Page 5: chunks ------------------------------------------------------------------
  matchReviewSummary(): Observable<MatchRunSummary> {
    return this.http.get<MatchRunSummary>(`${this.base}/v1/match-review/summary`);
  }

  listMatchReviewPatterns(operationId: string, category?: string | null): Observable<MatchReviewPatternPage> {
    return this.http.get<MatchReviewPatternPage>(`${this.base}/v1/match-review/patterns`, {
      params: params({ operation_id: operationId, category }),
    });
  }

  decideMatchReviewPattern(
    operationId: string,
    patternKey: string,
    body: { action: string; reviewer: string; reason: string; selected_values: string[] },
  ): Observable<unknown> {
    return this.http.post<unknown>(
      `${this.base}/v1/match-review/patterns/${encodeURIComponent(patternKey)}/decision`,
      body,
      { params: params({ operation_id: operationId }) },
    );
  }

  // --- Unresolved fields: population-first rule authoring ------------------------------
  // The screen drives itself from `refineRule`: one call returns the counts, the facets
  // and whether the population is coherent yet, so every edit needs exactly one request.

  listMatchChunkBuilds(): Observable<MatchChunkBuild[]> {
    return this.http.get<MatchChunkBuild[]>(`${this.base}/v1/match-review/builds`);
  }

  unresolvedOverview(buildId: string): Observable<UnresolvedOverview> {
    return this.http.get<UnresolvedOverview>(`${this.base}/v1/match-review/unresolved`, {
      params: params({ build_id: buildId }),
    });
  }

  unresolvedDiscriminators(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
  }): Observable<DiscriminatorReport> {
    return this.http.get<DiscriminatorReport>(
      `${this.base}/v1/match-review/unresolved/discriminators`,
      {
        params: params({
          build_id: options.buildId,
          source_field: options.sourceField,
          source_value: options.sourceValue,
        }),
      },
    );
  }

  unresolvedAttributes(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
  }): Observable<PopulationAttributes> {
    return this.http.get<PopulationAttributes>(
      `${this.base}/v1/match-review/unresolved/attributes`,
      {
        params: params({
          build_id: options.buildId,
          source_field: options.sourceField,
          source_value: options.sourceValue,
        }),
      },
    );
  }

  unresolvedValuePatterns(options: {
    buildId: string;
    sourceField: string;
    sourceValue: string;
    fieldName: string;
  }): Observable<PatternReport> {
    return this.http.get<PatternReport>(`${this.base}/v1/match-review/unresolved/patterns`, {
      params: params({
        build_id: options.buildId,
        source_field: options.sourceField,
        source_value: options.sourceValue,
        field_name: options.fieldName,
      }),
    });
  }

  /**
   * Suggests a rule for the filtered population. Writes nothing.
   *
   * Scoped by the filter rather than by a match-chunk build, so the model reasons
   * about the cars on screen. The build-scoped advisor it replaces saw a 226,529-row
   * slice, and reported "nothing to separate this" for populations that separate
   * perfectly well across the whole register.
   */
  adviseForFilter(body: {
    conditions: RuleCondition[];
    target_field: string;
  }): Observable<RuleAdvice> {
    return this.http.post<RuleAdvice>(`${this.base}/v1/ts-records/advise`, body);
  }

  targetVocabulary(buildId: string, targetField: string): Observable<TargetVocabulary> {
    return this.http.get<TargetVocabulary>(`${this.base}/v1/match-review/target-vocabulary`, {
      params: params({ build_id: buildId, target_field: targetField }),
    });
  }

  /** Live counts and facets for the predicate as it stands. Writes nothing. */
  refineRule(body: {
    build_id: string;
    source_field: string;
    source_value: string;
    conditions: RuleCondition[];
  }): Observable<RefineResult> {
    return this.http.post<RefineResult>(`${this.base}/v1/match-review/unresolved/refine`, body);
  }

  /** Dry run: counts what the rule would resolve. Writes nothing. */
  previewRule(body: {
    build_id: string;
    conditions: RuleCondition[];
    target_field: string;
    target_value: string;
    /** Count against cars that already carry a value, rather than only gaps. */
    override?: boolean;
  }): Observable<RulePreview> {
    return this.http.post<RulePreview>(`${this.base}/v1/match-review/rule-preview`, body);
  }

  /** Keeps a previewed rule. Saving alone resolves nothing; running it does. */
  saveResolutionRule(body: {
    build_id: string;
    source_field: string;
    source_value: string;
    conditions: RuleCondition[];
    target_field: string;
    target_value: string;
    author: string;
    note: string | null;
    /** Rewrite matched cars that already carry a different value. */
    override?: boolean;
  }): Observable<ResolutionRule> {
    return this.http.post<ResolutionRule>(`${this.base}/v1/match-review/resolution-rules`, body);
  }

  listResolutionRules(options: {
    buildId: string;
    sourceField?: string | null;
    sourceValue?: string | null;
  }): Observable<ResolutionRule[]> {
    return this.http.get<ResolutionRule[]>(`${this.base}/v1/match-review/resolution-rules`, {
      params: params({
        build_id: options.buildId,
        source_field: options.sourceField,
        source_value: options.sourceValue,
      }),
    });
  }

  /**
   * Starts running a saved rule over every car it covers.
   *
   * Returns as soon as the run is claimed, not when it finishes -- a rule covering two
   * hundred thousand cars takes about a minute. Poll `ruleApplication` until it settles.
   */
  applyResolutionRule(
    ruleId: string,
    reviewer: string,
  ): Observable<ResolutionRuleApplication> {
    return this.http.post<ResolutionRuleApplication>(
      `${this.base}/v1/match-review/resolution-rules/${encodeURIComponent(ruleId)}/apply`,
      { reviewer },
    );
  }

  ruleApplication(ruleId: string): Observable<ResolutionRuleApplication> {
    return this.http.get<ResolutionRuleApplication>(
      `${this.base}/v1/match-review/resolution-rules/${encodeURIComponent(ruleId)}/application`,
    );
  }

  /** Undoes a run: the rows it resolved reopen, the record of the rule stays. */
  retireResolutionRule(ruleId: string, reviewer: string): Observable<ResolutionRule> {
    return this.http.post<ResolutionRule>(
      `${this.base}/v1/match-review/resolution-rules/${encodeURIComponent(ruleId)}/retire`,
      { reviewer },
    );
  }

  // --- Filtering TS records (`/v1/ts-records`) ------------------------------------------
  // Every call takes the same condition shape the rule endpoints take, so a filter built
  // here can be handed to a rule without being rebuilt.

  countVehicles(filter: VehicleFilterRequest): Observable<VehicleCount> {
    return this.http.post<VehicleCount>(`${this.base}/v1/ts-records/count`, filter);
  }

  /** What the filtered set still cannot say about itself -- the worklist. */
  unresolvedSummary(filter: VehicleFilterRequest): Observable<UnresolvedSummary> {
    return this.http.post<UnresolvedSummary>(
      `${this.base}/v1/ts-records/unresolved-summary`,
      filter,
    );
  }

  vehicleFacet(
    filter: VehicleFilterRequest,
    field: string,
    limit = 12,
  ): Observable<VehicleFacet> {
    return this.http.post<VehicleFacet>(`${this.base}/v1/ts-records/facets`, filter, {
      params: params({ field, limit }),
    });
  }

  /** Keyset paging: pass the previous page's `next_cursor`, never an offset. */
  vehiclePage(
    filter: VehicleFilterRequest,
    options: { cursor?: number; limit?: number } = {},
  ): Observable<VehiclePage> {
    return this.http.post<VehiclePage>(`${this.base}/v1/ts-records/page`, filter, {
      params: params({ cursor: options.cursor ?? 0, limit: options.limit ?? 100 }),
    });
  }

  // --- NorthStar vehicles (`core.vehicles`) ---------------------------------------------

  /** Every column of the vehicle record: label, group, and whether it can be filtered. */
  vehicleFields(): Observable<VehicleFieldInfo[]> {
    return this.http.get<VehicleFieldInfo[]>(`${this.base}/v1/vehicles/fields`);
  }

  /** Vehicles by merged value, identifier (current or past) or NOR ID. Keyset-paged. */
  searchVehicles(
    request: VehicleSearchRequest,
    options: { cursor?: string | null; limit?: number } = {},
  ): Observable<NorVehiclePage> {
    return this.http.post<NorVehiclePage>(`${this.base}/v1/vehicles/search`, request, {
      params: params({ cursor: options.cursor ?? undefined, limit: options.limit ?? 50 }),
    });
  }

  /** Top values of one field inside the filter, counted as if it were not filtered on. */
  vehicleValues(
    request: VehicleSearchRequest,
    field: string,
    limit = 100,
  ): Observable<NorVehicleFacet> {
    return this.http.post<NorVehicleFacet>(`${this.base}/v1/vehicles/facets`, request, {
      params: params({ field, limit }),
    });
  }

  /** One vehicle: each value with its source, what lost, plates over time, links. */
  vehicleRecord(vehicleId: string): Observable<NorVehicleRecord> {
    return this.http.get<NorVehicleRecord>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}`,
    );
  }

  // --- TS-to-TecDoc matching diagnostics ------------------------------------------------

  /** Which KTypes one vehicle could be, matched on its merged values. */
  matchLookup(vehicleId: string): Observable<VehicleMatchLookup> {
    return this.http.get<VehicleMatchLookup>(`${this.base}/v1/vehicles/matching/lookup`, {
      params: params({ vehicle_id: vehicleId }),
    });
  }

  /**
   * Record a person's KType choice for one car: choose a candidate, "none of these",
   * or withdraw. Answers with the refreshed lookup (201 recorded, 200 replay of the same
   * operation id and content), so a retry must resend the same body.
   */
  recordKTypeChoice(vehicleId: string, body: KTypeChoiceRequest): Observable<VehicleMatchLookup> {
    return this.http.post<VehicleMatchLookup>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/ktype-choices`,
      body,
    );
  }

  /** A car's choices from the current one backwards; no matcher run. */
  ktypeChoiceHistory(vehicleId: string): Observable<KTypeChoiceHistory> {
    return this.http.get<KTypeChoiceHistory>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/ktype-choices`,
    );
  }

  /**
   * Record a person's correction to one field of one car: set a value, ignore the car's
   * value, or withdraw the correction. The field `normalization_stop` is not a value: ignoring
   * it releases a car that was stopped before matching. Answers with the lookup as matched
   * again after the write (201 recorded, 200 replay of the same operation id and content), so
   * a retry must resend the same body.
   */
  recordCorrection(vehicleId: string, body: FactCorrectionRequest): Observable<VehicleMatchLookup> {
    return this.http.post<VehicleMatchLookup>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/corrections`,
      body,
    );
  }

  /** A car's corrections per field, each from the current one backwards; no matcher run. */
  correctionHistory(vehicleId: string): Observable<FactCorrectionHistory> {
    return this.http.get<FactCorrectionHistory>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/corrections`,
    );
  }

  // --- One correction for several cars ---------------------------------------------------
  // Nothing is saved before a check: the matcher runs on each car of the scope as it is and
  // with the correction, and only what was checked can be applied.

  /** Whom a correction of this car could also apply to, with a count each. Saves nothing. */
  correctionScopes(vehicleId: string, body: CorrectionScopesRequest): Observable<CorrectionScopes> {
    return this.http.post<CorrectionScopes>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/corrections/scopes`,
      body,
    );
  }

  /**
   * Start checking what a correction would change for the cars of a scope. Answers with the
   * job (202); poll `correctionPreview` until its status settles. The server runs one check
   * at a time: a second is refused with 429 `busy`.
   */
  startCorrectionPreview(
    vehicleId: string,
    body: CorrectionPreviewRequest,
  ): Observable<CorrectionPreviewJob> {
    return this.http.post<CorrectionPreviewJob>(
      `${this.base}/v1/vehicles/${encodeURIComponent(vehicleId)}/corrections/preview`,
      body,
    );
  }

  correctionPreview(previewId: string): Observable<CorrectionPreviewJob> {
    return this.http.get<CorrectionPreviewJob>(
      `${this.base}/v1/vehicle-corrections/previews/${encodeURIComponent(previewId)}`,
    );
  }

  /** The checked cars of one outcome, each with where the matcher ends before and after. */
  correctionPreviewCars(
    previewId: string,
    outcome: CorrectionOutcome,
    limit: number,
  ): Observable<CorrectionPreviewCars> {
    return this.http.get<CorrectionPreviewCars>(
      `${this.base}/v1/vehicle-corrections/previews/${encodeURIComponent(previewId)}/cars`,
      { params: params({ outcome, limit }) },
    );
  }

  /** Stop a running check; it keeps what it found so far and frees the server for the next. */
  stopCorrectionPreview(previewId: string): Observable<unknown> {
    return this.http.delete<unknown>(
      `${this.base}/v1/vehicle-corrections/previews/${encodeURIComponent(previewId)}`,
    );
  }

  /**
   * Apply a checked correction to its cars, or keep it as a proposal that writes no car.
   * 201 when recorded, 200 for a replay of the same operation id, so a retry must resend
   * the same body.
   */
  decideCorrection(body: CorrectionDecisionRequest): Observable<CorrectionDecisionResult> {
    return this.http.post<CorrectionDecisionResult>(
      `${this.base}/v1/vehicle-corrections/decisions`,
      body,
    );
  }

  /** The decisions made for several cars at once, newest first. */
  correctionDecisions(limit = 100): Observable<CorrectionDecisionList> {
    return this.http.get<CorrectionDecisionList>(`${this.base}/v1/vehicle-corrections/decisions`, {
      params: params({ limit }),
    });
  }

  /** Undo a decision on every car it still stands on; cars a person changed since are left. */
  withdrawCorrectionDecision(
    decisionId: string,
    body: CorrectionWithdrawRequest,
  ): Observable<CorrectionWithdrawal> {
    return this.http.post<CorrectionWithdrawal>(
      `${this.base}/v1/vehicle-corrections/decisions/${encodeURIComponent(decisionId)}/withdraw`,
      body,
    );
  }

  /** Matching statistics of a Vehicles filter, read from stored results (no matcher run). */
  matchResultOverview(filter: {
    conditions: VehicleCondition[];
    text: string;
  }): Observable<MatchResultOverview> {
    return this.http.post<MatchResultOverview>(
      `${this.base}/v1/vehicles/match-results/overview`,
      filter,
    );
  }

  /** Match again every car that changed since its result was stored; runs on the server. */
  refreshMatchResults(): Observable<{ started: boolean; refreshing: boolean }> {
    return this.http.post<{ started: boolean; refreshing: boolean }>(
      `${this.base}/v1/vehicles/match-results/refresh`,
      null,
    );
  }

  /** The latest reviewer rules from the TS data screen, as changes to many cars. */
  reviewerRuleChanges(limit = 50): Observable<{ rules: ReviewerRuleChange[] }> {
    return this.http.get<{ rules: ReviewerRuleChange[] }>(
      `${this.base}/v1/vehicles/match-results/reviewer-rules`,
      { params: params({ limit }) },
    );
  }

  /** Cars per matching state under a Vehicles filter: one grouped query, for the strip. */
  matchResultCounts(filter: {
    conditions: VehicleCondition[];
    text: string;
  }): Observable<MatchResultCounts> {
    return this.http.post<MatchResultCounts>(
      `${this.base}/v1/vehicles/match-results/counts`,
      filter,
    );
  }

  vehicleDetail(sourceRecordId: number): Observable<VehicleDetail> {
    return this.http.get<VehicleDetail>(`${this.base}/v1/ts-records/${sourceRecordId}`);
  }
  /**
   * Where a gap lives, grouped by the shape of the value rather than its text.
   *
   * "Which cars are these" and "where is the leverage" are different questions, and an
   * exact-value list can only answer the first.
   */
  gapGroups(
    filter: VehicleFilterRequest,
    options: { field: string; mode?: GapGroupingMode; limit?: number },
  ): Observable<GapGroupReport> {
    return this.http.post<GapGroupReport>(`${this.base}/v1/ts-records/gap-groups`, filter, {
      params: params({
        field: options.field,
        mode: options.mode ?? 'leading_token',
        limit: options.limit ?? 25,
      }),
    });
  }
}
