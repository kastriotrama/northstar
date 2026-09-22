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
    { path: '/ts-data', label: 'TS data', hint: 'Registry rows, their gaps, and the rules that fill them' },
    { path: '/tecdoc', label: 'TecDoc', hint: 'Promoted TecDoc catalogue' },
    { path: '/vehicles', label: 'Vehicles', hint: 'Find cars by their canonical, normalized values' },
    { path: '/rules', label: 'Rules', hint: 'Normalization rules' },
    { path: '/chunks', label: 'Match review', hint: 'TS-to-TecDoc blocker evidence' },
  ];
}
