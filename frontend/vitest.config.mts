import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Deliberately minimal: this project keeps no broad frontend test suite, only
 * focused coverage of pure logic where a silent regression would be costly —
 * chart value formatting and presentation compatibility.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
