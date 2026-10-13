import React from 'react';
import { createRoot } from 'react-dom/client';

// Self-hosted fonts, bundled into the build — no CDN, per the product's
// egress posture (SPEC §11).
// Latin subsets only: the interface is English, and shipping the Cyrillic and
// Latin-Ext files would put megabytes into the image for nothing.
import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource/ibm-plex-sans/latin-700.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-500.css';
import '@fontsource/ibm-plex-mono/latin-600.css';

import './styles/tokens.css';
import './styles/base.css';
import './styles/components.css';
import './styles/app.css';
import './styles/workbench.css';

import App from './App';
import { applyPreviewBootstrap } from './api';

// The launcher's preview bootstrap record (if any) is applied before the first
// render: one authenticated client state, the same one the login flow uses.
applyPreviewBootstrap();

const container = document.getElementById('root');
if (!container) throw new Error('root container missing');
createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
