import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative paths, so the site works under https://<user>.github.io/<repo>/ without knowing the repo name.
export default defineConfig({
  base: "./",
  build: { chunkSizeWarningLimit: 700 },   // Observable Plot is most of it
  plugins: [react()],
});
