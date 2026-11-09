import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// In development the Python service runs separately (`qrs serve`); Vite proxies the API to it.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
  test: { environment: 'node' },
});
