import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  styleUrl: './app.scss',
  templateUrl: './app.html',
})
export class App {
  protected readonly links = [
    { path: '/coverage', label: 'Unresolved fields', hint: 'Author rules for unresolved values' },
    { path: '/ts-records', label: 'TS records', hint: 'Raw Transportstyrelsen rows' },
    { path: '/tecdoc', label: 'TecDoc', hint: 'Promoted TecDoc catalogue' },
    { path: '/rules', label: 'Rules', hint: 'Normalization rules' },
    { path: '/chunks', label: 'Match review', hint: 'TS-to-TecDoc blocker evidence' },
  ];
}
