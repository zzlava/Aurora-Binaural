import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const qa = path.join(root, "qa/p6/codex");
export default defineConfig({
  testDir: "./tests",
  testMatch: "rust-workflow.spec.ts",
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 30_000 },
  outputDir: path.join(qa, ".test-output/rust"),
  reporter: [["list"], ["json", { outputFile: path.join(qa, "rust-e2e-results.json") }]],
  use: {
    baseURL: "http://127.0.0.1:8776",
    browserName: "chromium",
    colorScheme: "light",
    viewport: { width: 1440, height: 1000 },
    trace: "off",
  },
  webServer: {
    command: "uv run aurora gui --no-browser --port 8776",
    cwd: root,
    url: "http://127.0.0.1:8776/health",
    reuseExistingServer: false,
    env: {
      // No engine override: exercise the user's default aurora gui path.
      AURORA_DATA_DIR: path.join(qa, ".runtime", `rust-${process.pid}`),
      AURORA_DB_PATH: path.join(qa, ".runtime", `rust-${process.pid}`, "aurora.db"),
      AURORA_WEB_DIR: path.join(root, "web/dist"),
    },
  },
});
