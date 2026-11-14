import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "", "SOUND_SYSTEM_");
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: {
        "/api": `http://127.0.0.1:${env.SOUND_SYSTEM_PORT ?? "8790"}`,
      },
    },
    build: {
      outDir: "dist",
    },
  };
});
