import { test, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const qa = `${root}/qa/p6/codex`;

test("滑块松手防抖 → Rust 自动试听保留 A/B 进度 → 下载报告", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const requests: number[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/v1/previews")) requests.push(Date.now());
  });
  const catalogResponse = page.waitForResponse((response) => response.url().endsWith("/v1/presets"));
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  const catalog = await (await catalogResponse).json();
  const defaults = catalog.presets.find((row: { id: string }) => row.id === catalog.default_preset).default_controls;
  await page.getByLabel("导入音频", { exact: true }).setInputFiles(`${root}/data/assets/real_music.wav`);
  await page.getByRole("button", { name: "生成试听片段", exact: true }).click();
  await expect(page.locator(".player")).toHaveAttribute("data-loaded-job", /^job_/);
  await page.locator(".advanced-controls summary").click();
  for (const [key, value] of Object.entries(defaults))
    await expect(page.locator(`#control-${key}`)).toHaveValue(String(value));
  await page.getByRole("button", { name: "B 效果" }).click();
  await page.getByRole("slider", { name: "试听播放位置", exact: true }).fill("2.5");
  const before = await page.locator(".player").getAttribute("data-loaded-job");
  const slider = page.locator("#control-width");
  await slider.scrollIntoViewIfNeeded();
  const box = (await slider.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.8, box.y + box.height / 2, { steps: 8 });
  await page.waitForTimeout(400); // Hold the pointer: no request is allowed before release.
  expect(requests).toHaveLength(1);
  const releasedAt = Date.now();
  await page.mouse.up();
  await expect(slider).not.toHaveValue(String(defaults.width));
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1] - releasedAt).toBeGreaterThanOrEqual(280);
  await expect(page.locator(".player")).not.toHaveAttribute("data-loaded-job", before!);
  await expect(page.getByTestId("playback-position")).toHaveText("00:02.5");
  await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute("aria-pressed", "true");
  const width = Number(await slider.inputValue());
  const preview = page.getByTestId("job-preview").first();
  await mkdir(`${qa}/controls-04`, { recursive: true });
  const audioDownload = page.waitForEvent("download");
  await preview.getByRole("button", { name: "下载音频", exact: true }).click();
  const audio = await audioDownload;
  await audio.saveAs(`${qa}/.runtime/controls-preview.wav`);
  const reportDownload = page.waitForEvent("download");
  await preview.getByRole("button", { name: "下载报告", exact: true }).click();
  await (await reportDownload).saveAs(`${qa}/controls-04/gui-preview.json`);
  const report = JSON.parse(await readFile(`${qa}/controls-04/gui-preview.json`, "utf8"));
  expect(report.engine.is_stub).toBe(false);
  expect(report.controls).toEqual({ ...defaults, width });
  expect(report.effective_params.controls).toEqual(report.controls);
  expect(report.timeline.sample_diff).toBe(0);
  expect((await readFile(`${qa}/.runtime/controls-preview.wav`)).length).toBeGreaterThan(1000);
  expect(errors).toEqual([]);
});
