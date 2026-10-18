import { defineConfig } from "vite";

export default defineConfig({
  build: {
    outDir: "dist",
    // Don't ship sourcemaps publicly.
    sourcemap: false,
  },
  server: {
    port: 3000,
  },
});
