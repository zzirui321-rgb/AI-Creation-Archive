import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { cpSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react(), {
    name: 'copy-review-fixtures',
    closeBundle() {
      const source = fileURLToPath(new URL('./demo/generated/', import.meta.url));
      if (existsSync(source)) cpSync(source, fileURLToPath(new URL('./dist/demo/generated/', import.meta.url)), { recursive: true });
    },
  }],
  test: { environment: 'jsdom', include: ['tests/**/*.test.ts', 'tests/**/*.test.tsx'], globals: false },
  server: { proxy: { '/edit-api': 'http://127.0.0.1:8102' } },
  build: { rollupOptions: { input: {
    main: fileURLToPath(new URL('./index.html', import.meta.url)),
    semanticEdit: fileURLToPath(new URL('./semantic-edit.html', import.meta.url)),
  } } },
});
