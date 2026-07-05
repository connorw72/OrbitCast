import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Static export: `vite build` emits a self-contained bundle to dist/ (no SSR
// server), deployable to Vercel's static tier (CLAUDE.md D11, §8).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
});
