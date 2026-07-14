import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// Minimal ambient declaration so we can read an env var without pulling in
// @types/node (kept out to respect the dependency age gate).
declare const process: { env: Record<string, string | undefined> };

// The backend the /api proxy targets. run.sh sets TINKERSCOPE_DEV_BACKEND to the
// dev backend port it launched; default 8765 keeps a bare `npm run dev` working.
const backendPort = process.env.TINKERSCOPE_DEV_BACKEND || '8765';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true
      }
    }
  }
});
