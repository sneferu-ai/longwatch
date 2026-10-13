import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// The SPA is served same-origin by FastAPI from longwatch/static/, which is
// also what the Dockerfile's `spa` stage copies into the runtime image.
export default defineConfig({
  plugins: [react()],
  build: {
    // the Dockerfile's spa stage redirects this with LONGWATCH_SPA_OUT
    outDir: process.env.LONGWATCH_SPA_OUT
      || fileURLToPath(new URL('../longwatch/static', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2020',
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000', '/healthz': 'http://127.0.0.1:8000' },
  },
});
