import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withFetch()),
    provideOptimus({
      theme: {
        preset: Aura,
        options: {
          // Keep Optimus's generated CSS below Angular's own layer so component styles win.
          cssLayer: { name: 'optimus', order: 'theme, base, optimus' },
          darkModeSelector: '.dark',
        },
      },
    }),
  ],
};
