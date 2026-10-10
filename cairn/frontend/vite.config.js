import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const outDir = process.env.VITE_OUT_DIR || "../src/cairn/server/static";

export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: {
    outDir,
    emptyOutDir: false,
    assetsDir: "assets",
  },
});
