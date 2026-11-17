import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// base './' so the same build works at / and at /w/<slug>/.
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
