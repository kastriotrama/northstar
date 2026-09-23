import { Routes } from '@angular/router';

/**
 * One page per concern in the pipeline: TS data and its normalization, TecDoc data,
 * looking a car up by its canonical values, the rules that govern them, and the
 * matching between them.
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
    path: 'vehicles',
    title: 'Vehicles',
    loadComponent: () => import('./pages/car-search/car-search').then((m) => m.CarSearchPage),
  },
  { path: 'car-search', pathMatch: 'full', redirectTo: 'vehicles' },
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
