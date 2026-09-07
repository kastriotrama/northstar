# NorthStar web

Angular front end for the vehicle-data platform. Replaces the two vanilla-JS review apps
(`api/app/features/normalization_review/static` and the match-review static app, ~5,900
lines across ten tabs) with **five pages**.

## The five pages

| Route | Page | Backing endpoints |
| --- | --- | --- |
| `/coverage` | **Unresolved fields** — population-first rule authoring and coverage | `GET /v1/match-review/unresolved`, `/discriminators`, `/rule-preview` *(backend port still required)* |
| `/ts-records` | **TS records** — all 7.25M raw Transportstyrelsen rows | `GET /v1/source-records/ts`, `/ts/{id}`, `/ts/batches`, `/ts/fields` *(new)* |
| `/tecdoc` | **TecDoc** — promoted catalogue, every field | `GET /v1/normalization-review/tecdoc/vehicles`, `/tecdoc/entities` |
| `/rules` | **Rules** — all normalization rules | `GET /v1/normalization-review/rules/catalog` *(new)* |
| `/chunks` | **Match review** — unresolved patterns and versioned rule proposals | `GET /v1/match-review/summary`, `/patterns`, `POST /patterns/{key}/decision` |

Five is the budget. New capability belongs inside one of these pages, not in a sixth.

## Stack

- **Angular 22.1.5** (latest stable), standalone components, signals, zoneless.
- **Optimus UI `@openng/optimus-ui` 2.0.2** — the MIT community continuation of PrimeNG,
  API-compatible with PrimeNG v21 (`primeng/button` → `@openng/optimus-ui/button`,
  `providePrimeNG` → `provideOptimus`, `@primeuix/themes/aura` →
  `@openng/optimus-ui-themes/aura`). Aura preset.
- TypeScript 6.0, Vitest + jsdom for tests.

## Requirements

**Node ≥ 22.22.3** (Angular CLI 22 refuses older). The machine's default `node` was v18.20.8 with
npm 6, which cannot build this app. Node 24.20.0 LTS is installed at
`~/.nvm/versions/node/v24.20.0`; put it on `PATH` first:

```bash
export PATH="$HOME/.nvm/versions/node/v24.20.0/bin:$PATH"
```

## Running

```bash
nx serve northstar-web            # http://127.0.0.1:4200
```

The API base URL defaults to `http://127.0.0.1:8010` (see `src/app/core/api-config.ts`) and
can be overridden at runtime without a rebuild:

```html
<script>window.__NORTHSTAR_API__ = 'http://127.0.0.1:8000';</script>
```

The backend must allow the dev origin:

```bash
CORS_ORIGINS=http://localhost:4200,http://127.0.0.1:4200
```

## Tests

```bash
nx test northstar-web
```

`src/app/pages/pages.integration.spec.ts` renders each page against a **live** API and
asserts real rows appear — the point is to prove the pages work against real data, not that
a fixture round-trips. Every case skips when the API is unreachable. Aim it elsewhere with
`NS_API_BASE`.

## Performance notes that matter

- **TS records uses keyset pagination**, never `OFFSET`. `OFFSET 5000000` on
  `staging.transportstyrelsen_raw` measured ~7.3s; the keyset read is ~0.01s. The page
  keeps a cursor stack so "previous" works without seeking backwards.
- **Search on that table is unindexed** (primary key only). A term matching nothing scans
  all 7.25M rows (~8s). The endpoint caps it with a statement timeout and the UI surfaces a
  "narrow your search" notice rather than hanging. A GIN index on `raw_record` would fix
  this properly but was not created — the disk was at 99%.
- **Coverage is per batch.** An all-parts view spans 261 batches / 6.5M rows and is a known
  timeout; `/v1/coverage/ts` rejects an `-all-parts` selector with 422 instead of hanging.
