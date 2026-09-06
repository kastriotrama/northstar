import { Routes } from '@angular/router';

/**
 * Five pages. That is the whole app, on purpose -- it replaces two vanilla-JS apps whose
 * ten tabs were the main complaint. New capability belongs inside one of these five.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'coverage' },
  {
    path: 'coverage',
    title: 'Unresolved fields',
    loadComponent: () => import('./pages/coverage/coverage').then((m) => m.CoveragePage),
  },
  {
    path: 'ts-records',
    title: 'TS records',
    loadComponent: () => import('./pages/ts-records/ts-records').then((m) => m.TsRecordsPage),
  },
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
  { path: '**', redirectTo: 'coverage' },
];
