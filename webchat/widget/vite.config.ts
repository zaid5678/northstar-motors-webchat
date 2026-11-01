import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

// One self-contained script for the website: <script src=".../widget.js" defer></script>
export default defineConfig({
  plugins: [react()],
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    lib: {
      entry: resolve(__dirname, "src/main.tsx"),
      name: "NorthstarChat",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
    outDir: resolve(__dirname, "../src/northstar_webchat/static"),
    emptyOutDir: false,
  },
  test: { environment: "jsdom", include: ["tests/**/*.test.tsx"] },
});
