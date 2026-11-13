import { defineConfig } from 'vite';
import { SERVER_HOST, SERVER_PORT, WEB_PORT } from './server/ports.ts';

// 127.0.0.1, never localhost: on Windows localhost resolves to [::1] first, and a stray process on that side
// answers instead of this one.
const target = `http://${SERVER_HOST}:${SERVER_PORT}`;

export default defineConfig({
  root: 'web',
  server: {
    host: SERVER_HOST,
    port: WEB_PORT,
    strictPort: true,
    // keys starting with ^ are regexes: a bare '/api' prefix would also take the module /api.ts
    proxy: { '^/api/': target, '^/files/': target },
  },
  build: { outDir: '../dist', emptyOutDir: true },
});
