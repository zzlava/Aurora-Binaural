import { test, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const qa = `${root}/qa/p6/codex`;

test("滑块松手防抖 → Rust 自动试听保留 A/B 进度 → 下载报告", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const starts: { when: number; offset: number }[] = [];
    Object.assign(window, { __controlStarts: starts });
    const original = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when = 0, offset = 0, duration?: number) {
      starts.push({ when, offset });
      original.call(this, when, offset, duration);
    };
  });
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
  // Replace a running pair too; both sources must restart at the current shared clock.
  await page.getByRole("button", { name: "播放试听", exact: true }).click();
  const runningJob = await page.locator(".player").getAttribute("data-loaded-job");
  await page.locator("#control-height").press("End");
  await expect(page.locator(".player")).not.toHaveAttribute("data-loaded-job", runningJob!);
  await expect(page.getByRole("button", { name: "暂停试听", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "暂停试听", exact: true }).click();
  const starts = await page.evaluate(() => (window as unknown as {
    __controlStarts: { when: number; offset: number }[];
  }).__controlStarts);
  expect(starts).toHaveLength(4);
  expect(starts[0]).toEqual(starts[1]);
  expect(starts[2]).toEqual(starts[3]);
  expect(starts[2].offset).toBeGreaterThan(starts[0].offset);
  await writeFile(`${qa}/controls-04/ab-evidence.json`, JSON.stringify({
    starts, releaseDelayMs: requests[1] - releasedAt, pausedPosition: "00:02.5", requests: requests.length,
  }, null, 2) + "\n");
  expect(errors).toEqual([]);
});


test("我的预设 SQLite 保存、改名、重启读回、JSON 往返与批量导出；桌面和窄屏", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  await page.locator(".advanced-controls summary").click();
  const settings = { strength: 0.65, width: 0.83, height: 0.42, room: 0.21, voice_distance: 0.12 };
  for (const [key, value] of Object.entries(settings)) {
    // Keyboard manipulation uses the native range's input/change events too.
    const slider = page.locator(`#control-${key}`);
    await slider.focus();
    await slider.press("Home");
    for (let i = 0; i < Math.round(value * 100); i++) await slider.press("ArrowRight");
  }
  await page.getByLabel("预设名称", { exact: true }).fill("夜听空间");
  await page.getByRole("button", { name: "保存为新预设", exact: true }).click();
  await expect(page.getByLabel("选用我的预设")).toContainText("夜听空间");
  await page.getByLabel("预设名称", { exact: true }).fill("夜听 · 近人声");
  await page.getByRole("button", { name: "重命名", exact: true }).click();
  await expect(page.getByLabel("选用我的预设")).toContainText("夜听 · 近人声");
  await page.reload();
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  const savedOption = page.getByLabel("选用我的预设").locator("option").filter({ hasText: "夜听 · 近人声" });
  const identity = (await savedOption.getAttribute("value"))!;
  await page.getByLabel("选用我的预设").selectOption(identity);
  await page.locator(".advanced-controls summary").click();
  for (const [key, value] of Object.entries(settings))
    await expect(page.locator(`#control-${key}`)).toHaveValue(String(value));
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出预设 JSON", exact: true }).click();
  await mkdir(`${qa}/controls-04`, { recursive: true });
  const jsonPath = `${qa}/controls-04/user-presets.json`;
  await (await exported).saveAs(jsonPath);
  const bundle = JSON.parse(await readFile(jsonPath, "utf8"));
  expect(bundle.schema_version).toBe(1);
  expect(bundle.presets[0].controls).toEqual(settings);
  await page.getByRole("button", { name: "删除预设", exact: true }).click();
  await expect(page.getByLabel("选用我的预设")).toBeDisabled();
  await page.getByLabel("导入预设 JSON 文件", { exact: true }).setInputFiles(jsonPath);
  await expect(page.getByLabel("选用我的预设")).toBeEnabled();
  const importedId = await page.getByLabel("选用我的预设").locator("option").filter({ hasText: "夜听 · 近人声" }).getAttribute("value");
  expect(importedId).not.toBe(identity);
  await page.getByLabel("选用我的预设").selectOption(importedId!);
  await page.getByLabel("导入音频", { exact: true }).setInputFiles([
    `${root}/data/assets/real_music.wav`, `${root}/data/assets/holdout_electronic.wav`,
  ]);
  await expect(page.locator(".asset-row")).toHaveCount(2);
  await page.getByRole("button", { name: "将全部 2 个素材加入导出队列", exact: true }).click();
  const exports = page.getByTestId("job-export");
  await expect(exports).toHaveCount(2);
  for (let i = 0; i < 2; i++) {
    await expect(exports.nth(i).getByText("已完成", { exact: true })).toBeVisible();
    const download = page.waitForEvent("download");
    await exports.nth(i).getByRole("button", { name: "下载报告", exact: true }).click();
    const path = `${qa}/controls-04/gui-batch-${i}.json`;
    await (await download).saveAs(path);
    const report = JSON.parse(await readFile(path, "utf8"));
    expect(report.controls).toEqual(settings);
    expect(report.effective_params.controls).toEqual(settings);
    expect(report.engine.is_stub).toBe(false);
  }
  await page.getByRole("button", { name: "恢复预设默认", exact: true }).click();
  await expect(page.locator("#control-width")).toHaveValue("0.5");
  await page.getByLabel("选用我的预设").selectOption(importedId!);
  await mkdir(`${qa}/screenshots-04`, { recursive: true });
  for (const size of [{ name: "desktop", width: 1440, height: 1000 }, { name: "narrow", width: 390, height: 844 }]) {
    await page.setViewportSize(size);
    for (const theme of ["light", "dark"]) {
      if (await page.locator("html").getAttribute("data-theme") !== theme)
        await page.getByRole("button", { name: theme === "dark" ? "切换深色模式" : "切换浅色模式" }).click();
      await page.evaluate(() => window.scrollTo(0, 0));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `${qa}/screenshots-04/${size.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    }
  }
  expect(errors).toEqual([]);
});

test("本地 GUI 使用远程渲染时，我的预设仍写入本机 SQLite", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "远程模式", exact: true }).click();
  await page.getByLabel("服务器地址", { exact: true }).fill("http://127.0.0.1:8779");
  await page.getByLabel("API Key", { exact: true }).fill("controls-remote-fixture");
  await page.getByRole("button", { name: "连接服务器", exact: true }).click();
  await expect(page.getByText("远程已连接", { exact: true })).toBeVisible();
  await page.getByLabel("预设名称", { exact: true }).fill("本机保留的预设");
  await page.getByRole("button", { name: "保存为新预设", exact: true }).click();
  await expect(page.getByLabel("选用我的预设")).toContainText("本机保留的预设");
  const remote = await request.get("http://127.0.0.1:8779/v1/user-presets", {
    headers: { Authorization: "Bearer controls-remote-fixture" },
  });
  expect(await remote.json()).toEqual([]);
  const session = await (await request.get("/gui/session", { headers: { "X-Aurora-GUI": "1" } })).json();
  const local = await request.get("/v1/user-presets", { headers: { Authorization: `Bearer ${session.api_key}` } });
  expect((await local.json()).some((row: { name: string }) => row.name === "本机保留的预设")).toBe(true);
});
