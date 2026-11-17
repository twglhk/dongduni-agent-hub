import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// base './': the same build is served at / and at /w/<slug>/ for other workspaces.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../agent_hub/board_next',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:8791', '/f': 'http://127.0.0.1:8791' },
  },
});
