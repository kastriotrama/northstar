import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DecimalPipe } from '@angular/common';
import { ButtonModule } from '@openng/optimus-ui/button';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { TableModule } from '@openng/optimus-ui/table';
import type { TableLazyLoadEvent } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';

import { Api } from '../../core/api';

interface ChunkBuild {
  build_id: string;
  source_batch_id: string;
  status: string;
  row_count: number;
  chunk_count: number;
  finished_at: string | null;
}

interface ChunkProgress {
  decided_rows: number;
  in_review_rows: number;
  member_rows: number;
  resolved_rows: number;
  applied_rules: number;
}

interface ChunkListItem {
  chunk_id: string;
  signature: Record<string, unknown>;
  member_count: number;
  reason_profile: Record<string, unknown>;
  status: string;
}

interface ChunkPage {
  build: ChunkBuild | null;
  total: number;
  decided_members: number;
  progress: ChunkProgress | null;
  items: ChunkListItem[];
}

@Component({
  selector: 'ns-chunks',
  imports: [
    FormsModule,
    DecimalPipe,
    ButtonModule,
    DialogModule,
    InputTextModule,
    TableModule,
    TagModule,
  ],
  templateUrl: './chunks.html',
})
export class ChunksPage {
  private readonly api = inject(Api);

  protected readonly page = signal<ChunkPage | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly query = signal('');
  protected readonly rows = 50;
  protected readonly first = signal(0);

  protected readonly detail = signal<unknown>(null);
  protected readonly detailOpen = signal(false);

  /** Signature keys present across the visible chunks — the matcher's grouping key. */
  protected readonly signatureColumns = computed(() => {
    const items = this.page()?.items ?? [];
    const keys = new Set<string>();
    for (const item of items) {
      Object.keys(item.signature ?? {}).forEach((key) => {
        if (key !== 'signature_version') {
          keys.add(key);
        }
      });
    }
    return [...keys];
  });

  constructor() {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.listChunks({ limit: this.rows, offset: this.first() }).subscribe({
      next: (response) => {
        this.page.set(response as unknown as ChunkPage);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.page.set(null);
        this.error.set(
          err?.status === 404
            ? 'The chunk endpoints are not present on this API build.'
            : 'Chunks could not be loaded.',
        );
      },
    });
  }

  protected onLazyLoad(event: TableLazyLoadEvent): void {
    this.first.set(event.first ?? 0);
    this.load();
  }

  protected open(chunk: ChunkListItem): void {
    this.detail.set(null);
    this.detailOpen.set(true);
    this.api.getChunk(chunk.chunk_id).subscribe({
      next: (result) => this.detail.set(result),
      error: () => this.detail.set({ error: 'Chunk detail unavailable.' }),
    });
  }

  protected signatureCell(chunk: ChunkListItem, column: string): string {
    const value = chunk.signature?.[column];
    if (value === null || value === undefined) {
      return '—';
    }
    return Array.isArray(value) ? value.join(', ') : String(value);
  }

  protected statusSeverity(status: string): 'success' | 'info' | 'warn' | 'secondary' {
    switch (status) {
      case 'resolved':
        return 'success';
      case 'proposed':
        return 'info';
      case 'open':
        return 'warn';
      default:
        return 'secondary';
    }
  }

  protected detailJson(): string {
    return JSON.stringify(this.detail(), null, 2);
  }
}
