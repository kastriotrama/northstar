import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { API_BASE_URL } from './api-config';
import type {
  CoverageBatch,
  MatchChunkPage,
  RuleCatalogResponse,
  SourceBatch,
  SourceFieldInventory,
  SourceRecordDetail,
  SourceRecordPage,
  TecDocCoverageReport,
  TecDocEntityPage,
  TecDocPage,
  TsCoverageReport,
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

  // --- Page 3: rules -------------------------------------------------------------------
  // Uses the paginated catalog, not GET /rules: that endpoint returns ~12MB in ~25s
  // because it also aggregates the newest normalization batch.
  listRules(options: {
    query?: string;
    area?: string | null;
    canonicalField?: string | null;
    decision?: string | null;
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
          limit: options.limit ?? 100,
          offset: options.offset ?? 0,
        }),
      },
    );
  }

  /** Rule smart creator: asks the match-review advisor to propose rules for a blocker. */
  adviseRules(body: Record<string, unknown>): Observable<unknown> {
    return this.http.post<unknown>(`${this.base}/v1/match-review/unresolved/advise`, body);
  }

  listUnresolvedPatterns(options: { limit?: number }): Observable<unknown> {
    return this.http.get<unknown>(`${this.base}/v1/match-review/unresolved/patterns`, {
      params: params({ limit: options.limit ?? 50 }),
    });
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
  listChunks(options: {
    limit?: number;
    offset?: number;
    [key: string]: unknown;
  }): Observable<MatchChunkPage> {
    return this.http.get<MatchChunkPage>(`${this.base}/v1/match-review/chunks`, {
      params: params({
        limit: (options.limit as number) ?? 50,
        offset: (options.offset as number) ?? 0,
      }),
    });
  }

  getChunk(chunkId: string): Observable<unknown> {
    return this.http.get<unknown>(`${this.base}/v1/match-review/chunks/${chunkId}`);
  }
}
