import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const qa = `${root}/qa/p6/codex/.runtime/p6-01-regression`;

test("真实音乐 → Rust V2 试听与同步 A/B → WAV 导出下载；四种主题视口", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    const starts: { when: number; offset: number; length: number; sampleRate: number; pcm: number[] }[] = [];
    Object.assign(window, { __p6Sources: starts });
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when = 0, offset = 0, duration?: number) {
      const buffer = this.buffer;
      starts.push({
        when, offset, length: buffer?.length ?? 0, sampleRate: buffer?.sampleRate ?? 0,
        // A bounded PCM window proves real A/B differences without retaining full buffers.
        pcm: Array.from(buffer?.getChannelData(0).slice(24000, 25024) ?? []),
      });
      nativeStart.call(this, when, offset, duration);
    };
  });
  const catalogPromise = page.waitForResponse((response) => response.url().endsWith("/v1/presets"));
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  const catalog = await (await catalogPromise).json();
  await expect(page.locator(".engine-notice")).toContainText(`路线 V · 预设 ${catalog.default_preset} · HRTF SADIE II D1`);
  await expect(page.locator(".engine-notice")).not.toContainText("桩");
  await expect(page.locator(`input[name="preset"][value="${catalog.default_preset}"]`)).toBeChecked();
  // Confirm classic route selection is reflected without executing another algorithm.
  await page.locator('input[name="preset"][value="room"]').check();
  await expect(page.locator(".engine-notice")).toContainText("路线 B · 预设 room");
  await page.locator('input[name="preset"][value="V2"]').check();
  await page.getByLabel("导入音频", { exact: true }).setInputFiles([
    `${root}/data/assets/real_music.wav`, `${root}/data/assets/holdout_electronic.wav`,
  ]);
  await expect(page.locator(".asset-row")).toHaveCount(2);
  await mkdir(`${qa}/downloads`, { recursive: true });
  await mkdir(`${qa}/screenshots`, { recursive: true });
  await mkdir(`${qa}/gui-reports`, { recursive: true });
  const evidence = [];
  for (const filename of ["real_music", "holdout_electronic"]) {
    await page.locator(".asset-row").filter({ hasText: `${filename}.wav` }).click();
    await page.getByLabel("起始位置（秒）").fill("1");
    await page.getByLabel("试听时长（秒）").fill("10");
    await page.getByRole("button", { name: "生成试听片段", exact: true }).click();
    const preview = page.getByTestId("job-preview").filter({ hasText: `${filename}.wav` });
    await expect(preview.getByText("已完成", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "播放试听", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "播放试听", exact: true }).click();
    await expect.poll(async () => page.getByTestId("playback-position").innerText()).not.toBe("00:00.0");
    await page.getByRole("button", { name: "B 效果" }).click();
    await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("a");
    await expect(page.getByRole("button", { name: "A 原声" })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("b");
    await page.getByRole("button", { name: "暂停试听", exact: true }).click();
    const comparison = await page.evaluate(() => {
      const starts = (window as unknown as { __p6Sources: { when: number; offset: number; length: number; sampleRate: number; pcm: number[] }[] }).__p6Sources;
      const [a, b] = starts.slice(-2);
      return {
        totalSources: starts.length, a: { when: a.when, offset: a.offset, length: a.length, sampleRate: a.sampleRate },
        b: { when: b.when, offset: b.offset, length: b.length, sampleRate: b.sampleRate },
        differenceRms: Math.sqrt(a.pcm.reduce((sum, v, i) => sum + (v - b.pcm[i]) ** 2, 0) / a.pcm.length),
      };
    });
    expect(comparison.a).toEqual(comparison.b);
    expect(comparison.a.length).toBe(comparison.a.sampleRate * 10);
    expect(comparison.differenceRms).toBeGreaterThan(1e-5);
    expect(comparison.totalSources).toBe(evidence.length * 2 + 2); // A/B toggles do not restart sources.
    await expect(page.getByRole("button", { name: "B 效果" }).locator(".stub-label")).toHaveCount(0);
    const previewReportPromise = page.waitForEvent("download");
    await preview.getByRole("button", { name: "下载报告", exact: true }).click();
    await (await previewReportPromise).saveAs(`${qa}/gui-reports/${filename}.preview.json`);
    await page.getByRole("button", { name: "导出当前素材", exact: true }).click();
    const exported = page.getByTestId("job-export").filter({ hasText: `${filename}.wav` });
    await expect(exported.getByText("已完成", { exact: true })).toBeVisible();
    await expect(exported.locator(".report-summary")).toContainText("Rust 0.1.0 · 路线 V · 预设 V2");
    const audioDownload = page.waitForEvent("download");
    await exported.getByRole("button", { name: "下载音频", exact: true }).click();
    const downloaded = await audioDownload;
    expect(downloaded.suggestedFilename()).toBe(`${filename}.wav`);
    await downloaded.saveAs(`${qa}/downloads/${filename}.V2.wav`);
    const reportDownload = page.waitForEvent("download");
    await exported.getByRole("button", { name: "下载报告", exact: true }).click();
    await (await reportDownload).saveAs(`${qa}/gui-reports/${filename}.export.json`);
    evidence.push({ filename, comparison });
  }
  for (const size of [
    { name: "desktop", width: 1440, height: 1000 }, { name: "narrow", width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const theme of ["light", "dark"]) {
      if (await page.locator("html").getAttribute("data-theme") !== theme)
        await page.getByRole("button", { name: theme === "dark" ? "切换深色模式" : "切换浅色模式" }).click();
      await page.evaluate(() => window.scrollTo(0, 0));
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: `${qa}/screenshots/${size.name}-${theme}.png`, fullPage: true, animations: "disabled" });
    }
  }
  expect(errors).toEqual([]);
  await writeFile(`${qa}/gui-evidence.json`, JSON.stringify({ errors, evidence }, null, 2) + "\n");
});
