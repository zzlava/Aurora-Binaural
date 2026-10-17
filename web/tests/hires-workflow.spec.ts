import { test, expect } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../..", import.meta.url));
const qa = `${root}/qa/p6/codex`;

test("高解析 FLAC → 源采样率 FLAC、ALAC、AIFF、float WAV 实际下载回读", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const catalogPromise = page.waitForResponse((response) => response.url().endsWith("/v1/presets"));
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
  const catalog = await (await catalogPromise).json();
  await expect(page.locator(`input[name="preset"][value="${catalog.default_preset}"]`)).toBeChecked();
  await page.getByLabel("导入音频", { exact: true }).setInputFiles(`${qa}/.runtime/p6-02/impulse-96000.flac`);
  await expect(page.locator(".asset-row")).toHaveCount(1);
  await expect(page.getByRole("combobox", { name: "采样率", exact: true })).toHaveValue("source");
  const evidence = [];
  await mkdir(`${qa}/downloads-02`, { recursive: true });
  const rows = [
    { format: "flac", rate: "source", depth: "24", extension: "flac", sr: 96000, codec: "flac", bits: 24, samples: 192017 },
    { format: "alac", rate: "44100", depth: "24", extension: "m4a", sr: 44100, codec: "alac", bits: 24, samples: Math.round(192017 * 44100 / 96000) },
    { format: "aiff", rate: "48000", depth: "16", extension: "aiff", sr: 48000, codec: "pcm_s16be", bits: 16, samples: Math.round(192017 / 2) },
    { format: "wav", rate: "96000", depth: "32f", extension: "wav", sr: 96000, codec: "pcm_f32le", bits: 32, samples: 192017 },
  ];
  for (const [index, row] of rows.entries()) {
    await page.getByRole("combobox", { name: "格式", exact: true }).selectOption(row.format);
    await page.getByRole("combobox", { name: "采样率", exact: true }).selectOption(row.rate);
    await page.getByRole("combobox", { name: "位深", exact: true }).selectOption(row.depth);
    const requestPromise = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/v1/jobs"));
    await page.getByRole("button", { name: "导出当前素材", exact: true }).click();
    const payload = (await requestPromise).postDataJSON();
    expect(payload.output).toEqual({
      format: row.format, sample_rate: row.rate === "source" ? "source" : Number(row.rate),
      bit_depth: row.depth === "32f" ? "32f" : Number(row.depth),
    });
    await expect(page.getByTestId("job-export")).toHaveCount(index + 1);
    const job = page.getByTestId("job-export").first();
    await expect(job.getByText("已完成", { exact: true })).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await job.getByRole("button", { name: "下载音频", exact: true }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe(`impulse-96000.${row.extension}`);
    const output = `${qa}/downloads-02/gui-${row.format}.${row.extension}`;
    await download.saveAs(output);
    const readback = JSON.parse(execFileSync(`${root}/.venv/bin/python`, ["-c",
      "import sys; from pathlib import Path; from aurora_binaural.media import probe_audio; print(probe_audio(Path(sys.argv[1])).model_dump_json())", output,
    ], { cwd: root, encoding: "utf8" }));
    expect(readback.sample_rate).toBe(row.sr);
    expect(Math.abs(readback.samples - row.samples)).toBeLessThanOrEqual(1);
    expect(readback.codec).toBe(row.codec);
    expect(readback.bit_depth).toBe(row.bits);
    evidence.push({ settings: payload.output, download: download.suggestedFilename(), readback });
  }
  await page.screenshot({ path: `${qa}/screenshots/hires-desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("combobox", { name: "位深", exact: true })).toBeVisible();
  await page.screenshot({ path: `${qa}/screenshots/hires-narrow.png`, fullPage: true });
  expect(errors).toEqual([]);
  await writeFile(`${qa}/gui-evidence-02.json`, JSON.stringify({ errors, evidence }, null, 2) + "\n");
});
