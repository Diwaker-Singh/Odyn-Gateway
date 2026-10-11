import { defineConfig } from "vite";

export default defineConfig({
  base: process.env.ODYN_BASE_PATH || "/",
  build: {
    sourcemap: false,
    target: "es2022",
  },
});
