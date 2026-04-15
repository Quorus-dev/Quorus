import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  build: {
    outDir: "dist",
    // Don't ship sourcemaps publicly. Flip back to "hidden" or true only
    // when wiring Sentry / similar upload-at-deploy-time pipelines.
    sourcemap: false,
  },
  server: {
    port: 3000,
  },
});
