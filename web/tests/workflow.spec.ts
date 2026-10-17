import { test, expect, type Page } from "@playwright/test";
import { Buffer } from "node:buffer";
import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

declare global {
  interface Window {
    __abStarts: {
      when: number;
      offset: number;
      length: number;
      sampleRate: number;
    }[];
  }
}

const screenshotDir = fileURLToPath(
  new URL("../../qa/p6/codex/.runtime/stub-screenshots/", import.meta.url),
);
function wav(duration = 5): Buffer {
  const rate = 48000,
    samples = rate * duration,
    channels = 2;
  const out = Buffer.alloc(44 + samples * channels * 2);
  out.write("RIFF", 0);
  out.writeUInt32LE(out.length - 8, 4);
  out.write("WAVEfmt ", 8);
  out.writeUInt32LE(16, 16);
  out.writeUInt16LE(1, 20);
  out.writeUInt16LE(channels, 22);
  out.writeUInt32LE(rate, 24);
  out.writeUInt32LE(rate * channels * 2, 28);
  out.writeUInt16LE(channels * 2, 32);
  out.writeUInt16LE(16, 34);
  out.write("data", 36);
  out.writeUInt32LE(samples * channels * 2, 40);
  for (let i = 0; i < samples; i++) {
    const value = Math.round(7000 * Math.sin((2 * Math.PI * 440 * i) / rate));
    out.writeInt16LE(value, 44 + i * 4);
    out.writeInt16LE(-value, 46 + i * 4);
  }
  return out;
}
async function open(page: Page) {
  await page.goto("/");
  await expect(page.getByText("本地已连接", { exact: true })).toBeVisible();
}
async function importAudio(page: Page, name = "夜间声景.wav", duration = 5) {
  await page
    .getByLabel("导入音频", { exact: true })
    .setInputFiles({ name, mimeType: "audio/wav", buffer: wav(duration) });
  await expect(
    page.locator(".asset-row").filter({ hasText: name }),
  ).toBeVisible();
}
function position(text: string): number {
  const [minutes, seconds] = text.split(":").map(Number);
  return minutes * 60 + seconds;
}

test("上传 → 源时间试听 → 同步 A/B 与快捷键 → 导出下载；四种视口截图", async ({
  page,
}) => {
  const errors: string[] = [],
    requests: string[] = [];
  await page.addInitScript(() => {
    window.__abStarts = [];
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (
      when = 0,
      offset = 0,
      duration?: number,
    ) {
      window.__abStarts.push({
        when,
        offset,
        length: this.buffer?.length ?? 0,
        sampleRate: this.buffer?.sampleRate ?? 0,
      });
      nativeStart.call(this, when, offset, duration);
    };
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => requests.push(request.url()));
  const catalogPromise = page.waitForResponse((response) => response.url().endsWith("/v1/presets"));
  await open(page);
  const catalog = await (await catalogPromise).json();
  await expect(page.getByText("效果引擎：桩（直通）")).toBeVisible();
  await expect(page.locator(".developer")).not.toHaveAttribute("open");
  await expect(page.locator(".preset-option")).toHaveCount(4);
  await expect(page.locator(".preset-option")).not.toContainText(["缓慢环绕"]);
  await expect(page.locator(`input[name="preset"][value="${catalog.default_preset}"]`)).toBeChecked();
  await importAudio(page);
  await page.getByRole("button", { name: "本地模式", exact: true }).click();
  await expect(page.locator(".asset-row")).toHaveCount(1);
  await page.locator('input[name="preset"][value="V1"]').check();
  await page.getByLabel("空间强度", { exact: true }).fill("0.6");
  await page.getByLabel("起始位置（秒）").fill("1");
  await page.getByLabel("试听时长（秒）").fill("3");
  await page.getByRole("button", { name: "生成试听片段", exact: true }).click();
  const preview = page.getByTestId("job-preview").first();
  await expect(preview.getByText("已完成", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "播放试听", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "播放试听", exact: true }).click();
  // Focus remains on Play: letter shortcuts must still work.
  await page.keyboard.press("b");
  await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.getByRole("button", { name: "暂停试听", exact: true }),
  ).toBeVisible();
  await expect
    .poll(async () =>
      position(await page.getByTestId("playback-position").innerText()),
    )
    .toBeGreaterThan(0.3);
  const before = position(
    await page.getByTestId("playback-position").innerText(),
  );
  await page.getByRole("button", { name: "B 效果" }).click();
  await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const after = position(
    await page.getByTestId("playback-position").innerText(),
  );
  expect(after).toBeGreaterThanOrEqual(before - 0.1);
  expect(after - before).toBeLessThan(0.6);
  await page.keyboard.press("a");
  await expect(page.getByRole("button", { name: "A 原声" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.keyboard.press("b");
  await expect(page.getByRole("button", { name: "B 效果" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const starts = await page.evaluate(() => window.__abStarts);
  expect(starts).toHaveLength(2);
  expect(starts[0]).toEqual(starts[1]); // Same clock, offset and buffer length; switching never restarts sources.
  await page.keyboard.press("Space");
  await expect(
    page.getByRole("button", { name: "播放试听", exact: true }),
  ).toBeVisible();
  const paused = await page.getByTestId("playback-position").innerText();
  await page.waitForTimeout(160);
  expect(await page.getByTestId("playback-position").innerText()).toBe(paused);
  await page.getByRole("button", { name: "导出当前素材", exact: true }).click();
  const exported = page.getByTestId("job-export").first();
  await expect(exported.getByText("已完成", { exact: true })).toBeVisible();
  await expect(exported.locator(".report-summary")).toContainText(
    "240,000 样本",
  );
  await expect(exported.locator(".report-summary")).toContainText("SADIE II");
  const downloadPromise = page.waitForEvent("download");
  await exported.getByRole("button", { name: "下载音频", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("夜间声景.wav");
  const saved = await download.path();
  expect(saved).toBeTruthy();
  const output = await readFile(saved!);
  expect(output.toString("ascii", 0, 4)).toBe("RIFF");
  expect(output.length).toBeGreaterThan(1_000_000);
  await page.getByRole("button", { name: "本地模式", exact: true }).click();
  await expect(page.getByTestId("job-preview")).toHaveCount(1);
  await expect(page.getByTestId("job-export")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "播放试听", exact: true }),
  ).toBeEnabled();
  await mkdir(screenshotDir, { recursive: true });
  for (const size of [
    { name: "desktop", width: 1440, height: 1000 },
    { name: "narrow", width: 390, height: 844 },
  ]) {
    await page.setViewportSize(size);
    for (const theme of ["light", "dark"]) {
      const current = await page.locator("html").getAttribute("data-theme");
      if (current !== theme)
        await page
          .getByRole("button", {
            name: theme === "dark" ? "切换深色模式" : "切换浅色模式",
          })
          .click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: `${screenshotDir}/${size.name}-${theme}.png`,
        fullPage: true,
        animations: "disabled",
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    }
  }
  expect(errors).toEqual([]);
  expect(
    requests.every(
      (url) =>
        url.startsWith("http://127.0.0.1:8767/") || url.startsWith("blob:"),
    ),
  ).toBe(true);
});

test("远程错误 Key 明确提示；Key 只在 sessionStorage，URL 与日志均无 Key", async ({
  page,
}) => {
  await open(page);
  const badKey = "incorrect-e2e-fixture-value";
  const urls: string[] = [],
    consoleText: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  page.on("console", (message) => consoleText.push(message.text()));
  await page.getByRole("button", { name: "远程模式", exact: true }).click();
  await page
    .getByLabel("服务器地址", { exact: true })
    .fill("http://127.0.0.1:8768");
  await page.getByLabel("API Key", { exact: true }).fill(badKey);
  await page.getByRole("button", { name: "连接服务器", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("API Key 不正确或已失效");
  expect(await page.evaluate(() => sessionStorage.getItem("aurora.key"))).toBe(
    badKey,
  );
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    badKey,
  );
  await page.locator(".developer summary").click();
  expect(await page.locator(".developer").innerText()).not.toContain(badKey);
  expect(urls.join("\n")).not.toContain(badKey);
  expect(page.url()).not.toContain(badKey);
  expect(consoleText.join("\n")).not.toContain(badKey);
  await page.getByRole("button", { name: "清除 Key", exact: true }).click();
  expect(
    await page.evaluate(() => sessionStorage.getItem("aurora.key")),
  ).toBeNull();
  await page
    .getByLabel("API Key", { exact: true })
    .fill("aurora-e2e-fixture-key");
  await page.getByRole("button", { name: "连接服务器", exact: true }).click();
  await expect(page.getByText("远程已连接", { exact: true })).toBeVisible();
  await importAudio(page, "远程测试.wav", 1);
  await page.getByRole("button", { name: "远程模式", exact: true }).click();
  await expect(page.locator(".asset-row")).toHaveCount(1);
  await page.getByRole("button", { name: "导出当前素材", exact: true }).click();
  await expect(
    page.getByTestId("job-export").getByText("已完成", { exact: true }),
  ).toBeVisible();
});

test("取消正在运行的任务，状态稳定且不提供下载", async ({ page }) => {
  await open(page);
  await importAudio(page, "取消测试.wav", 12);
  await page.getByRole("button", { name: "导出当前素材", exact: true }).click();
  const job = page.getByTestId("job-export").first();
  await expect(job.getByText("处理中", { exact: true })).toBeVisible();
  await job.getByRole("button", { name: "取消任务", exact: true }).click();
  await expect(job.getByText("已取消", { exact: true })).toBeVisible();
  await expect(
    job.getByRole("button", { name: "下载音频", exact: true }),
  ).toHaveCount(0);
});

test("多文件导入与批量导出；无效音频显示可恢复错误", async ({ page }) => {
  await open(page);
  await page.getByLabel("导入音频", { exact: true }).setInputFiles([
    { name: "短音频一.wav", mimeType: "audio/wav", buffer: wav(1) },
    { name: "短音频二.wav", mimeType: "audio/wav", buffer: wav(1) },
    {
      name: "损坏音频.wav",
      mimeType: "audio/wav",
      buffer: Buffer.from("invalid audio"),
    },
  ]);
  await expect(page.locator(".asset-row")).toHaveCount(2);
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "关闭提示", exact: true }).click();
  await page
    .getByRole("button", { name: "将全部 2 个素材加入导出队列" })
    .click();
  await expect(page.getByTestId("job-export")).toHaveCount(2);
  await expect(
    page.getByTestId("job-export").filter({ hasText: "已完成" }),
  ).toHaveCount(2);
});
