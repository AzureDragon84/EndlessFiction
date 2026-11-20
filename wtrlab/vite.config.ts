import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The preview hostname is supplied per-process through
// __VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS instead of being committed here.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 3001,
    strictPort: true,
  },
  preview: {
    host: true,
    port: 3001,
    strictPort: true,
  },
});
