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
    { path: '/coverage', label: 'Unresolved', hint: 'Field coverage and gaps' },
    { path: '/ts-records', label: 'TS records', hint: 'Raw Transportstyrelsen rows' },
    { path: '/tecdoc', label: 'TecDoc', hint: 'Promoted TecDoc catalogue' },
    { path: '/rules', label: 'Rules', hint: 'Normalization rules' },
    { path: '/chunks', label: 'Chunks', hint: 'Match review' },
  ];
}
