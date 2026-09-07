import { Routes } from '@angular/router';

/**
 * Four pages, one per concern in the pipeline: TS data and its normalization, TecDoc
 * data, the rules that govern both, and the matching between them.
 *
 * Browsing a population and saying what it means were two pages until they were one
 * workflow with a handoff between them, which was the tell. New capability belongs
 * inside one of these four.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'ts-data' },
  {
    path: 'ts-data',
    title: 'TS data',
    loadComponent: () => import('./pages/ts-records/ts-records').then((m) => m.TsRecordsPage),
  },
  // The two screens this replaced; existing links keep working.
  { path: 'ts-records', pathMatch: 'full', redirectTo: 'ts-data' },
  { path: 'coverage', pathMatch: 'full', redirectTo: 'ts-data' },
  {
    path: 'tecdoc',
    title: 'TecDoc',
    loadComponent: () => import('./pages/tecdoc/tecdoc').then((m) => m.TecDocPage),
  },
  {
    path: 'rules',
    title: 'Rules',
    loadComponent: () => import('./pages/rules/rules').then((m) => m.RulesPage),
  },
  {
    path: 'chunks',
    title: 'Chunks',
    loadComponent: () => import('./pages/chunks/chunks').then((m) => m.ChunksPage),
  },
  { path: '**', redirectTo: 'ts-data' },
];
