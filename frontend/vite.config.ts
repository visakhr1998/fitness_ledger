import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // Built into the Python package so a clone runs with Python alone -- no
    // Node stage needed when this is containerised later.
    outDir: "../src/fitness_ledger/web/dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Dev server talks to the FastAPI backend; in production FastAPI serves
    // these same assets, so the app never needs to know which mode it is in.
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
