import { defineConfig } from 'vite';

export default defineConfig({
  clearScreen: false,
  resolve: { dedupe: ['echarts', 'zrender'] },
  server: {
    host: '127.0.0.1',
    port: 1420,
    strictPort: true,
    watch: { ignored: ['**/src-tauri/**', '**/plugins/**', '**/core/**', '**/.runtime/**'] },
  },
  build: { target: 'es2022', rollupOptions: { output: { manualChunks: (id) => id.includes('/node_modules/zrender/') ? 'chart-renderer' : id.includes('/node_modules/echarts/') ? 'charts-vendor' : undefined } } },
});
