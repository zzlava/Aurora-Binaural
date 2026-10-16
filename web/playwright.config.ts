import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("..", import.meta.url));
const qa = path.join(root, "qa/p4/antigravity");
const runtime = (name: string) => ({
  AURORA_DATA_DIR: path.join(qa, ".runtime", `${name}-${process.pid}`),
  AURORA_DB_PATH: path.join(qa, ".runtime", `${name}-${process.pid}`, "aurora.db"),
  AURORA_WORKER_BLOCK_DELAY_MS: "4",
  AURORA_HEARTBEAT_INTERVAL_SEC: "0.1",
  AURORA_CLEANUP_INTERVAL_SEC: "0.2",
  AURORA_WEB_DIR: path.join(root, "web/dist"),
});
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  outputDir: path.join(qa, ".test-output"),
  reporter: [
    ["list"],
    ["json", { outputFile: path.join(qa, "e2e-results.json") }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8767",
    browserName: "chromium",
    colorScheme: "light",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "off",
  },
  webServer: [
    {
      command: "uv run aurora gui --no-browser --port 8767",
      cwd: root,
      url: "http://127.0.0.1:8767/health",
      timeout: 30_000,
      reuseExistingServer: false,
      env: runtime("local"),
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: "uv run python qa/p4/antigravity/e2e_remote_server.py",
      cwd: root,
      url: "http://127.0.0.1:8768/health",
      timeout: 30_000,
      reuseExistingServer: false,
      env: {
        ...runtime("remote"),
        AURORA_API_KEY: "aurora-e2e-fixture-key",
        AURORA_CORS_ORIGINS: "http://127.0.0.1:8767",
      },
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
