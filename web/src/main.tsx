import { render } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { Api, ApiError, serverAddress } from "./api";
import { ABPlayer } from "./player";
import type { Asset, EngineStatus, Job, Preset, QueueEntry, Report } from "./types";
import "./style.css";

const statuses: Record<Job["status"], string> = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  canceled: "已取消",
};
const seconds = (value: number) =>
  `${Math.floor(value / 60)
    .toString()
    .padStart(2, "0")}:${(value % 60).toFixed(1).padStart(4, "0")}`;
const lufs = (value: number | null | undefined) =>
  value == null ? "静音" : `${value.toFixed(1)} LUFS`;
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";
function readSession(key: string): string {
  try {
    return sessionStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}
function writeSession(key: string, value: string): void {
  try {
    if (value) sessionStorage.setItem(key, value);
    else sessionStorage.removeItem(key);
  } catch {
    /* Memory-only fallback when storage is disabled. */
  }
}

function App() {
  const [theme, setTheme] = useState(() =>
    window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light",
  );
  const [mode, setMode] = useState<"local" | "remote">("local");
  const [server, setServer] = useState(
    readSession("aurora.server") || window.location.origin,
  );
  const [key, setKey] = useState(readSession("aurora.key"));
  const [api, setApi] = useState<Api | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [engine, setEngine] = useState<EngineStatus | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [preset, setPreset] = useState("");
  const [strength, setStrength] = useState(0.5);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [queue, setQueue] = useState<QueueEntry[]>([]);
  const [start, setStart] = useState(0);
  const [duration, setDuration] = useState(10);
  const [format, setFormat] = useState("wav");
  const [rate, setRate] = useState(48000);
  const [tail, setTail] = useState("trim");
  const [busy, setBusy] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [loaded, setLoaded] = useState<QueueEntry | null>(null);
  const [autoLoadId, setAutoLoadId] = useState("");
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [side, setSide] = useState<0 | 1>(0);
  const player = useRef(new ABPlayer());
  const epoch = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const active = assets.find((asset) => asset.asset_id === selectedId);
  const selectedPreset = presets.find((item) => item.id === preset);
  const pending = busy.length > 0;
  const activeJobs = queue.filter((entry) =>
    ["queued", "running"].includes(entry.job.status),
  ).length;

  function log(message: string) {
    setLogs((previous) => [
      ...previous.slice(-119),
      `${new Date().toLocaleTimeString("zh-CN")}  ${message}`,
    ]);
  }
  function redact(message: string) {
    return key ? message.split(key).join("[REDACTED]") : message;
  }
  async function perform(name: string, action: () => Promise<void>) {
    setBusy((previous) => [...previous, name]);
    setError("");
    try {
      await action();
    } catch (failure) {
      setError(redact(errorText(failure)));
    } finally {
      setBusy((previous) => previous.filter((item) => item !== name));
    }
  }
  function resetConnection() {
    epoch.current++;
    setApi(null);
    setEngine(null);
    setAssets([]);
    setSelectedId("");
    setQueue([]);
    setPresets([]);
    player.current.dispose();
    setLoaded(null);
    setPlaying(false);
    setPosition(0);
    setAutoLoadId("");
  }
  async function connect(nextMode: "local" | "remote") {
    resetConnection();
    setConnecting(true);
    setError("");
    const connectionEpoch = epoch.current;
    try {
      let base = "";
      let token = "";
      if (nextMode === "local") {
        const response = await fetch("/gui/session", {
          headers: { "X-Aurora-GUI": "1" },
          cache: "no-store",
        });
        if (!response.ok)
          throw new ApiError(
            "本地服务未启动。请使用 aurora gui，或切换远程模式连接服务器。",
          );
        token = (await response.json()).api_key;
      } else {
        base = serverAddress(server);
        if (!key.trim()) throw new ApiError("请填写 API Key。");
        token = key.trim();
        writeSession("aurora.key", token);
        writeSession("aurora.server", base);
      }
      const client = new Api(base, token, log);
      const result = await client.request<{
        presets: Preset[];
        default_preset: string;
        engine: EngineStatus;
      }>("/v1/presets");
      if (epoch.current !== connectionEpoch) return;
      setPresets(result.presets);
      setEngine(result.engine);
      const chosen =
        result.presets.find((item) => item.id === preset) ??
        result.presets.find((item) => item.id === result.default_preset) ??
        result.presets[0];
      if (chosen) {
        setPreset(chosen.id);
        setStrength(chosen.default_strength);
      }
      setApi(client);
      setNotice("连接成功，可以导入音频。");
      log("连接成功");
    } catch (failure) {
      if (epoch.current === connectionEpoch)
        setError(redact(errorText(failure)));
    } finally {
      if (epoch.current === connectionEpoch) setConnecting(false);
    }
  }
  useEffect(() => {
    void connect("local");
    return () => {
      epoch.current++;
      player.current.dispose();
    };
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      setPosition(player.current.position);
      setPlaying(player.current.playing);
    }, 80);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!api) return;
    let disposed = false;
    let polling = false;
    const connectionEpoch = epoch.current;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        await Promise.all(
          queue
            .filter(
              (entry) =>
                ["queued", "running"].includes(entry.job.status) ||
                (entry.job.status === "succeeded" && !entry.report),
            )
            .map(async (entry) => {
              try {
                const job = await api.request<Job>(
                  `/v1/jobs/${entry.job.job_id}`,
                );
                const report =
                  job.status === "succeeded"
                    ? await api.request<Report>(`/v1/jobs/${job.job_id}/report`)
                    : undefined;
                if (!disposed && epoch.current === connectionEpoch)
                  setQueue((previous) =>
                    previous.map((item) =>
                      item.job.job_id === job.job_id
                        ? { ...item, job, report, issue: undefined }
                        : item,
                    ),
                  );
              } catch (failure) {
                if (!disposed && epoch.current === connectionEpoch)
                  setQueue((previous) =>
                    previous.map((item) =>
                      item.job.job_id === entry.job.job_id
                        ? { ...item, issue: errorText(failure) }
                        : item,
                    ),
                  );
              }
            }),
        );
      } finally {
        polling = false;
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 700);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [api, queue]);
  useEffect(() => {
    const ready = queue.find(
      (entry) =>
        entry.job.job_id === autoLoadId &&
        entry.job.status === "succeeded" &&
        entry.report,
    );
    if (ready) {
      setAutoLoadId("");
      void loadPreview(ready);
    }
  }, [queue, autoLoadId]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.repeat ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        !loaded
      )
        return;
      const element = event.target as HTMLElement | null;
      if (element?.closest('input,select,textarea,[contenteditable="true"]'))
        return;
      if (event.code === "Space") {
        // Preserve native activation elsewhere; player buttons share the transport shortcut.
        if (element?.closest("button,a,summary") && !element.closest(".player"))
          return;
        event.preventDefault();
        void togglePlayback();
      }
      if (event.key.toLowerCase() === "a") switchSide(0);
      if (event.key.toLowerCase() === "b") switchSide(1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [loaded]);

  async function upload(files: FileList | File[]) {
    if (!api) {
      setError("请先连接服务，再导入文件。");
      return;
    }
    const client = api,
      connectionEpoch = epoch.current;
    await perform("upload", async () => {
      let failed = 0;
      for (const file of Array.from(files)) {
        try {
          setNotice(`正在导入 ${file.name}…`);
          const asset = await client.request<Asset>("/v1/assets", {
            method: "POST",
            headers: {
              "Content-Type": "application/octet-stream",
              "X-Asset-Filename": encodeURIComponent(file.name),
            },
            body: file,
          });
          if (epoch.current !== connectionEpoch) return;
          setAssets((previous) => [...previous, asset]);
          setSelectedId(asset.asset_id);
          setStart(0);
          setDuration(Math.min(10, asset.duration_seconds));
        } catch (failure) {
          failed++;
          if (epoch.current === connectionEpoch) setError(errorText(failure));
        }
      }
      if (epoch.current === connectionEpoch)
        setNotice(
          failed
            ? `${failed} 个文件导入失败，其余文件已保留，可重新选择失败文件。`
            : "导入完成，选择预设后生成试听。",
        );
    });
  }
  async function createJob(preview: boolean, all = false) {
    if (!api || !active || !selectedPreset) return;
    const client = api,
      connectionEpoch = epoch.current;
    await perform(preview ? "preview" : "export", async () => {
      const targets = all ? assets : [active];
      for (const asset of targets) {
        if (
          preview &&
          (!Number.isFinite(start) ||
            !Number.isFinite(duration) ||
            start < 0 ||
            start >= asset.duration_seconds ||
            duration <= 0 ||
            duration > 20)
        ) {
          throw new ApiError(
            "试听起点必须在音频内，时长需大于 0 且不超过 20 秒。",
          );
        }
        const settings = {
          preset: { id: preset, version: selectedPreset.version },
          strength,
          seed: 20261016,
        };
        const job = await client.json<Job>(
          preview ? "/v1/previews" : "/v1/jobs",
          preview
            ? {
                asset_id: asset.asset_id,
                start_seconds: start,
                duration_seconds: duration,
                ...settings,
              }
            : {
                input: { asset_id: asset.asset_id },
                ...settings,
                output: {
                  format,
                  sample_rate: rate,
                  bit_depth: format === "wav" ? 24 : null,
                },
                timing: { mode: "preserve", tail },
              },
        );
        if (epoch.current !== connectionEpoch) return;
        setQueue((previous) => [
          { job, filename: asset.filename },
          ...previous,
        ]);
        if (preview) setAutoLoadId(job.job_id);
      }
      setNotice(
        preview
          ? "试听任务已加入队列，完成后自动载入 A/B 播放器。"
          : "完整音频已加入导出队列。",
      );
    });
  }
  async function loadPreview(entry: QueueEntry) {
    if (!api) return;
    const client = api,
      connectionEpoch = epoch.current;
    await perform("player", async () => {
      player.current.pause();
      setPlaying(false);
      setLoaded(null);
      const [original, effect] = await Promise.all([
        client.request<Blob>(`/v1/jobs/${entry.job.job_id}/original`, {}, true),
        client.request<Blob>(`/v1/jobs/${entry.job.job_id}/result`, {}, true),
      ]);
      if (epoch.current !== connectionEpoch) return;
      await player.current.load(original, effect);
      if (epoch.current !== connectionEpoch) {
        player.current.dispose();
        return;
      }
      player.current.switchTo(0);
      setSide(0);
      setPosition(0);
      setLoaded(entry);
      setNotice(
        entry.report?.engine.is_stub
          ? "A/B 试听已就绪。当前引擎为直通桩，尚无空间音效。"
          : "A/B 试听已就绪。可切换原声与效果，播放进度保持同步。",
      );
    });
  }
  async function togglePlayback() {
    await perform("playback", async () => {
      if (player.current.playing) player.current.pause();
      else await player.current.play();
      setPlaying(player.current.playing);
      setPosition(player.current.position);
    });
  }
  function switchSide(next: 0 | 1) {
    player.current.switchTo(next);
    setSide(next);
  }
  async function cancel(entry: QueueEntry) {
    if (!api) return;
    await perform(`cancel-${entry.job.job_id}`, async () => {
      const job = await api.request<Job>(
        `/v1/jobs/${entry.job.job_id}/cancel`,
        { method: "POST" },
      );
      setQueue((previous) =>
        previous.map((item) =>
          item.job.job_id === job.job_id ? { ...item, job } : item,
        ),
      );
    });
  }
  const connectionLocked = connecting || pending;
  const displayedJson = redact(
    JSON.stringify(
      { connected: !!api, selected_asset: active, jobs: queue },
      null,
      2,
    ),
  );

  return (
    <div class="app-shell">
      <header class="masthead">
        <a class="brand" href="/" aria-label="Aurora Binaural 首页">
          <svg
            width="30"
            height="32"
            viewBox="0 0 30 32"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M3 24V12M9 29V4M15 24V9M21 28V3M27 22V12"
              stroke="currentColor"
              stroke-width="3"
              stroke-linecap="round"
            />
          </svg>
          <span>
            Aurora <span class="brand-light">Binaural</span>
          </span>
        </a>
        <div class="header-actions">
          <span
            class={`connection-status ${api ? "online" : ""}`}
            role="status"
          >
            {connecting
              ? "正在连接…"
              : api
                ? mode === "local"
                  ? "本地已连接"
                  : "远程已连接"
                : "未连接"}
          </span>
          <button
            class="quiet theme-toggle"
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            aria-label={theme === "light" ? "切换深色模式" : "切换浅色模式"}
          >
            {theme === "light" ? "深色" : "浅色"}
          </button>
        </div>
      </header>
      <main>
        <div class="page-title">
          <div>
            <h1>听见更开阔的空间</h1>
            <p>导入、试听、对比，再导出。保留音频的原始时间线。</p>
          </div>
          <span class="dataset">
            默认 HRTF <strong>SADIE II</strong>
          </span>
        </div>
        <div class="engine-notice" role="note">
          <strong>{engine?.message ?? "正在读取效果引擎状态…"}</strong>
          <span>
            {engine?.is_stub
              ? "当前使用桩，仅验证处理流程，未应用 HRTF。"
              : engine?.available
                ? `路线 ${selectedPreset?.route ?? "—"} · 预设 ${selectedPreset?.id ?? "—"} · HRTF ${engine.hrtf?.name ?? "未知"} · 核心 ${engine.version ?? "未知"}`
                : engine
                  ? "请安装 Rust 核心后再生成试听或导出。"
                  : "连接后显示引擎、路线与预设。"}
          </span>
        </div>
        <section class="connection-bar" aria-label="连接设置">
          <div class="mode-control" role="group" aria-label="服务模式">
            <button
              aria-pressed={mode === "local"}
              disabled={connectionLocked}
              onClick={() => {
                if (mode === "local") return;
                setMode("local");
                void connect("local");
              }}
            >
              本地模式
            </button>
            <button
              aria-pressed={mode === "remote"}
              disabled={connectionLocked}
              onClick={() => {
                if (mode === "remote") return;
                setMode("remote");
                resetConnection();
                setError("");
                setNotice(
                  "填写服务器地址与 API Key 后连接。切换连接会清空本页列表，服务器任务仍会继续。",
                );
              }}
            >
              远程模式
            </button>
          </div>
          {mode === "remote" ? (
            <form
              class="remote-form"
              onSubmit={(event) => {
                event.preventDefault();
                void connect("remote");
              }}
            >
              <label>
                服务器地址
                <input
                  type="url"
                  value={server}
                  placeholder="https://audio.example.com"
                  disabled={connectionLocked || !!api}
                  onInput={(event) => setServer(event.currentTarget.value)}
                  required
                />
              </label>
              <label>
                API Key
                <input
                  type="password"
                  autoComplete="off"
                  spellcheck={false}
                  value={key}
                  disabled={connectionLocked || !!api}
                  onInput={(event) => {
                    setKey(event.currentTarget.value);
                    writeSession("aurora.key", event.currentTarget.value);
                  }}
                  required
                />
              </label>
              <button type="submit" disabled={connectionLocked || !!api}>
                {connecting ? "连接中…" : "连接服务器"}
              </button>
              <button
                type="button"
                class="quiet"
                disabled={connectionLocked}
                onClick={() => {
                  resetConnection();
                  setKey("");
                  writeSession("aurora.key", "");
                  setNotice("API Key 已从当前会话清除。");
                }}
              >
                清除 Key
              </button>
              <small>
                Key 仅在当前标签页会话中保留。跨域连接需在服务器配置
                AURORA_CORS_ORIGINS。
              </small>
            </form>
          ) : (
            <div class="local-info">
              <span>API 与 Worker 仅监听 127.0.0.1</span>
              {!api && (
                <button
                  class="quiet"
                  disabled={connectionLocked}
                  onClick={() => void connect("local")}
                >
                  重新连接
                </button>
              )}
            </div>
          )}
        </section>
        {error && (
          <div class="error-banner" role="alert">
            <strong>操作未完成</strong>
            <span>{error}</span>
            <button class="quiet" onClick={() => setError("")}>
              关闭提示
            </button>
          </div>
        )}
        {notice && (
          <p class="notice" role="status">
            {notice}
          </p>
        )}
        <div class="workspace">
          <section class="source-pane" aria-labelledby="source-heading">
            <div class="section-heading">
              <h2 id="source-heading">素材</h2>
              <span>{assets.length} 个文件</span>
            </div>
            <div
              class={`drop-zone ${dragging ? "dragging" : ""}`}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (event.dataTransfer?.files && !pending)
                  void upload(event.dataTransfer.files);
              }}
            >
              <svg
                width="32"
                height="32"
                viewBox="0 0 32 32"
                fill="none"
                aria-hidden="true"
              >
                <path
                  d="M16 21V5m0 0-6 6m6-6 6 6M5 21v6h22v-6"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linejoin="round"
                />
              </svg>
              <strong>
                {busy.includes("upload")
                  ? "正在上传与分析…"
                  : "拖入音频，开始试听"}
              </strong>
              <span>WAV、M4A、MP3、AAC · 支持多选</span>
              <button
                disabled={!api || pending}
                onClick={() => fileInput.current?.click()}
              >
                {busy.includes("upload") ? "导入中…" : "选择音频文件"}
              </button>
              <input
                ref={fileInput}
                class="visually-hidden"
                type="file"
                aria-label="导入音频"
                accept=".wav,.m4a,.mp3,.aac,audio/*"
                multiple
                disabled={!api || pending}
                onChange={(event) => {
                  if (event.currentTarget.files)
                    void upload(event.currentTarget.files);
                  event.currentTarget.value = "";
                }}
              />
            </div>
            <div class="asset-list" aria-label="素材列表">
              {assets.map((asset) => (
                <button
                  key={asset.asset_id}
                  class={`asset-row ${asset.asset_id === selectedId ? "selected" : ""}`}
                  aria-pressed={asset.asset_id === selectedId}
                  onClick={() => {
                    setSelectedId(asset.asset_id);
                    setStart(0);
                    setDuration(Math.min(10, asset.duration_seconds));
                  }}
                >
                  <span class="file-mark" aria-hidden="true">
                    {asset.filename
                      .split(".")
                      .pop()
                      ?.toUpperCase()
                      .slice(0, 4) || "音频"}
                  </span>
                  <span class="asset-name">
                    <strong>{asset.filename}</strong>
                    <small>
                      {seconds(asset.duration_seconds)} ·{" "}
                      {(asset.sample_rate / 1000).toFixed(1)} kHz ·{" "}
                      {asset.channels === 1 ? "单声道" : "立体声"}
                    </small>
                  </span>
                </button>
              ))}
            </div>
            {!assets.length && (
              <p class="empty-note">
                素材会保留在当前服务中，处理完成后可下载音频和报告。
              </p>
            )}
          </section>
          <section class="editor-pane" aria-labelledby="settings-heading">
            <div class="section-heading">
              <h2 id="settings-heading">空间设置</h2>
              <span>预设 v{selectedPreset?.version ?? "1"}</span>
            </div>
            <fieldset
              class="preset-fieldset"
              disabled={!api || !presets.length}
            >
              <legend class="visually-hidden">选择空间预设</legend>
              {presets.map((item) => (
                <label
                  key={item.id}
                  class={`preset-option ${preset === item.id ? "chosen" : ""}`}
                >
                  <input
                    type="radio"
                    name="preset"
                    value={item.id}
                    checked={preset === item.id}
                    onChange={() => {
                      setPreset(item.id);
                      setStrength(item.default_strength);
                    }}
                  />
                  <strong>
                    {item.id} · {item.name}
                  </strong>
                  <span>{item.description}</span>
                </label>
              ))}
            </fieldset>
            <div class="strength-row">
              <label for="strength">空间强度</label>
              <output for="strength">{Math.round(strength * 100)}%</output>
            </div>
            <input
              id="strength"
              type="range"
              min="0"
              max="1"
              step="0.01"
              value={strength}
              disabled={!api}
              onInput={(event) =>
                setStrength(Number(event.currentTarget.value))
              }
            />
            <div class="range-endpoints">
              <span>轻柔</span>
              <span>鲜明</span>
            </div>
            <div class="preview-settings">
              <label>
                起始位置（秒）
                <input
                  type="number"
                  min="0"
                  max={
                    active ? Math.max(0, active.duration_seconds - 0.001) : 0
                  }
                  step="0.1"
                  value={start}
                  disabled={!active || pending}
                  onInput={(event) =>
                    setStart(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <label>
                试听时长（秒）
                <input
                  type="number"
                  min="0.1"
                  max="20"
                  step="0.1"
                  value={duration}
                  disabled={!active || pending}
                  onInput={(event) =>
                    setDuration(event.currentTarget.valueAsNumber)
                  }
                />
              </label>
              <button
                class="primary"
                disabled={!active || pending}
                onClick={() => void createJob(true)}
              >
                {busy.includes("preview") ? "正在创建…" : "生成试听片段"}
              </button>
            </div>
            <p class="helper">
              最长 20 秒。运动效果沿用原音频中的时间位置；末尾不足时自动截短。
            </p>
            <section
              class="player"
              aria-label="等响度 A/B 播放器"
              aria-busy={busy.includes("player")}
            >
              <div class="player-title">
                <h3>等响度 A/B 对比</h3>
                <span class="match-label">BS.1770</span>
              </div>
              <p class="player-file">
                {busy.includes("player")
                  ? "正在下载并解码两侧音频…"
                  : loaded
                    ? `${loaded.filename} · ${seconds(loaded.job.preview?.start_seconds ?? 0)} 起`
                    : "生成试听后，两侧音频会自动载入"}
              </p>
              <div class="ab-switch" role="group" aria-label="A/B 音源">
                <button
                  aria-pressed={side === 0}
                  disabled={!loaded}
                  onClick={() => switchSide(0)}
                >
                  <kbd>A</kbd> 原声
                </button>
                <button
                  aria-pressed={side === 1}
                  disabled={!loaded}
                  onClick={() => switchSide(1)}
                >
                  <kbd>B</kbd> 效果
                  {loaded?.report?.engine.is_stub && (
                    <span class="stub-label">直通桩</span>
                  )}
                </button>
              </div>
              <div class="transport">
                <button
                  class="play-button"
                  aria-label={playing ? "暂停试听" : "播放试听"}
                  disabled={!loaded || busy.includes("playback")}
                  onClick={() => void togglePlayback()}
                >
                  {playing ? (
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 18 18"
                      aria-hidden="true"
                    >
                      <path
                        d="M5 3v12M13 3v12"
                        stroke="currentColor"
                        stroke-width="3"
                      />
                    </svg>
                  ) : (
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 18 18"
                      aria-hidden="true"
                    >
                      <path d="m5 2 11 7-11 7z" fill="currentColor" />
                    </svg>
                  )}
                </button>
                <div class="seek">
                  <input
                    type="range"
                    aria-label="试听播放位置"
                    min="0"
                    max={player.current.duration || 1}
                    step="0.01"
                    value={position}
                    disabled={!loaded}
                    onInput={(event) => {
                      player.current.seek(Number(event.currentTarget.value));
                      setPosition(player.current.position);
                    }}
                  />
                  <div class="timecode">
                    <span data-testid="playback-position">
                      {seconds(position)}
                    </span>
                    <span>{seconds(player.current.duration)}</span>
                  </div>
                </div>
                <label class="volume">
                  音量
                  <input
                    type="range"
                    aria-label="试听音量"
                    min="0"
                    max="1"
                    step="0.01"
                    defaultValue="0.7"
                    onInput={(event) =>
                      player.current.setVolume(
                        Number(event.currentTarget.value),
                      )
                    }
                  />
                </label>
              </div>
              <div class="player-meta">
                <span>
                  {loaded?.report?.preview?.match_status === "both_silent"
                    ? "两侧均为静音，无法计算响度差"
                    : loaded?.report
                      ? `原声 ${lufs(loaded.report.preview?.original_loudness.integrated_lufs)} / 效果 ${lufs(loaded.report.loudness_after.integrated_lufs)} · 差 ${(loaded.report.preview?.loudness_diff_lu ?? 0).toFixed(1)} LU`
                      : "同一播放时钟 · 切换保持进度"}
                </span>
                <span>空格 播放 / 暂停 · A / B 切换</span>
              </div>
            </section>
            <div class="export-controls">
              <h3>导出完整音频</h3>
              <div class="export-fields">
                <label>
                  格式
                  <select
                    value={format}
                    disabled={!active || pending}
                    onChange={(event) => {
                      const next = event.currentTarget.value;
                      setFormat(next);
                      if (next === "mp3" && rate === 96000) setRate(48000);
                    }}
                  >
                    <option value="wav">WAV · 24 bit</option>
                    <option value="m4a">M4A · AAC 256 kbps</option>
                    <option value="mp3">MP3 · 256 kbps</option>
                  </select>
                </label>
                <label>
                  采样率
                  <select
                    value={rate}
                    disabled={!active || pending}
                    onChange={(event) =>
                      setRate(Number(event.currentTarget.value))
                    }
                  >
                    <option value="44100">44.1 kHz</option>
                    <option value="48000">48 kHz</option>
                    <option value="96000" disabled={format === "mp3"}>
                      96 kHz
                    </option>
                  </select>
                </label>
                <label>
                  尾音
                  <select
                    value={tail}
                    disabled={!active || pending}
                    onChange={(event) => setTail(event.currentTarget.value)}
                  >
                    <option value="trim">裁切 · 保持原长度</option>
                    <option value="preserve">保留尾音</option>
                  </select>
                </label>
                <button
                  class="primary"
                  disabled={!active || pending}
                  onClick={() => void createJob(false)}
                >
                  {busy.includes("export") ? "正在创建…" : "导出当前素材"}
                </button>
              </div>
              {assets.length > 1 && (
                <button
                  class="batch-button quiet"
                  disabled={pending || !api}
                  onClick={() => void createJob(false, true)}
                >
                  将全部 {assets.length} 个素材加入导出队列
                </button>
              )}
            </div>
          </section>
        </div>
        <section class="queue-section" aria-labelledby="queue-heading">
          <div class="section-heading">
            <h2 id="queue-heading">任务队列</h2>
            <span>
              {activeJobs
                ? `${activeJobs} 个任务处理中`
                : `${queue.length} 个任务`}
            </span>
          </div>
          {!queue.length ? (
            <div class="queue-empty">
              还没有任务。导入音频后，生成一段试听或导出完整音频。
            </div>
          ) : (
            <div class="queue-list">
              {queue.map((entry) => (
                <article
                  key={entry.job.job_id}
                  class="job-row"
                  data-testid={`job-${entry.job.preview ? "preview" : "export"}`}
                >
                  <div class="job-main">
                    <div class="job-title">
                      <strong>{entry.filename}</strong>
                      <span class="job-kind">
                        {entry.job.preview ? "试听" : "完整导出"}
                      </span>
                      <span class={`job-status ${entry.job.status}`}>
                        {entry.job.canceling
                          ? "正在取消…"
                          : statuses[entry.job.status]}
                      </span>
                    </div>
                    <div class="job-progress">
                      <progress
                        aria-label={`${entry.filename} ${entry.job.preview ? "试听" : "导出"}进度`}
                        max="1"
                        value={entry.job.progress}
                      />
                      <span>{Math.round(entry.job.progress * 100)}%</span>
                    </div>
                    {entry.report && (
                      <p class="report-summary">
                        {(entry.report.output.sample_rate / 1000).toFixed(1)}{" "}
                        kHz ·{" "}
                        {entry.report.output.samples.toLocaleString("zh-CN")}{" "}
                        样本 ·{" "}
                        {lufs(entry.report.loudness_before.integrated_lufs)} →{" "}
                        {lufs(entry.report.loudness_after.integrated_lufs)} ·
                        HRTF：
                        {entry.report.engine.is_stub &&
                        entry.report.engine.hrtf.name.includes("SADIE")
                          ? "SADIE II（桩未应用）"
                          : entry.report.engine.hrtf.name}
                        <span>
                          {entry.report.engine.is_stub
                            ? "直通桩 · "
                            : `Rust ${entry.report.engine.version} · 路线 ${entry.report.engine.route ?? "未知"} · `}
                          预设 {entry.report.params.preset.id} v
                          {entry.report.params.preset.version} · 强度{" "}
                          {Math.round(entry.report.params.strength * 100)}% ·
                          seed {entry.report.params.seed}
                        </span>
                      </p>
                    )}
                    {entry.job.error && (
                      <div class="job-error" role="alert">
                        <strong>{entry.job.error.code}</strong>
                        <p>{entry.job.error.message}</p>
                        <details>
                          <summary>错误详情</summary>
                          <pre>
                            {redact(
                              JSON.stringify(entry.job.error.details, null, 2),
                            )}
                          </pre>
                        </details>
                      </div>
                    )}
                    {entry.issue && (
                      <p class="job-error" role="alert">
                        状态或报告读取失败：{entry.issue}，正在重试。
                      </p>
                    )}
                  </div>
                  <div class="job-actions">
                    {["queued", "running"].includes(entry.job.status) && (
                      <button
                        class="quiet"
                        disabled={
                          entry.job.canceling ||
                          busy.includes(`cancel-${entry.job.job_id}`)
                        }
                        onClick={() => void cancel(entry)}
                      >
                        {busy.includes(`cancel-${entry.job.job_id}`)
                          ? "取消中…"
                          : "取消任务"}
                      </button>
                    )}
                    {entry.job.status === "succeeded" && (
                      <>
                        {entry.job.preview && (
                          <button
                            class="quiet"
                            disabled={pending}
                            onClick={() => void loadPreview(entry)}
                          >
                            试听对比
                          </button>
                        )}
                        <button
                          disabled={busy.includes(
                            `download-${entry.job.job_id}`,
                          )}
                          onClick={() =>
                            void perform(
                              `download-${entry.job.job_id}`,
                              async () => {
                                await api?.download(
                                  `/v1/jobs/${entry.job.job_id}/result`,
                                  `${entry.filename.replace(/\.[^.]+$/, "")}.${entry.job.preview ? "preview.wav" : (entry.job.result_metadata?.format ?? "wav")}`,
                                );
                              },
                            )
                          }
                        >
                          {busy.includes(`download-${entry.job.job_id}`)
                            ? "下载中…"
                            : "下载音频"}
                        </button>
                        <button
                          class="quiet"
                          disabled={busy.includes(`report-${entry.job.job_id}`)}
                          onClick={() =>
                            void perform(
                              `report-${entry.job.job_id}`,
                              async () => {
                                await api?.download(
                                  `/v1/jobs/${entry.job.job_id}/report`,
                                  `${entry.job.job_id}.report.json`,
                                );
                              },
                            )
                          }
                        >
                          {busy.includes(`report-${entry.job.job_id}`)
                            ? "下载中…"
                            : "下载报告"}
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
        <details class="developer">
          <summary>
            开发者区域 <span>原始 JSON 与请求日志</span>
          </summary>
          <div class="debug-columns">
            <div>
              <h3>会话与任务 JSON</h3>
              <pre>{displayedJson}</pre>
            </div>
            <div>
              <h3>日志</h3>
              <pre>{redact(logs.join("\n")) || "尚无请求"}</pre>
            </div>
          </div>
        </details>
      </main>
      <footer>
        <span>Aurora Binaural</span>
        <span>戴上耳机，留意声音的位置。当前为流程验证版本。</span>
      </footer>
    </div>
  );
}

render(<App />, document.getElementById("app")!);
