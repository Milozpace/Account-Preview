import { defineConfig } from 'vite';
export default defineConfig({
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765', changeOrigin: true,
        headers: { Origin: 'http://127.0.0.1:8765' },
      },
    },
  },
  build: {
    target: 'es2022',
    rollupOptions: {
      output: {
        manualChunks: (id) => id.includes('/node_modules/zrender/')
          ? 'chart-renderer'
          : id.includes('/node_modules/echarts/') ? 'charts-vendor'
          : id.includes('/node_modules/') ? 'chart-runtime' : undefined,
      },
    },
  },
});
