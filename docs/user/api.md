# HTTP API v1

API 与 Worker 共用持久化 SQLite 和文件目录。`aurora api` 不自动启动 Worker。服务自带 `/openapi.json`、`/docs`、`/redoc`；完整模型也可用 `aurora openapi` 导出。

## 鉴权与端点

所有 `/v1/*` 必须提供 `Authorization: Bearer $AURORA_API_KEY`；未配置服务端 Key 或 Key 错误均为 401。没有多用户隔离，持有同一 Key 的客户端共享素材和任务。`/health` 无鉴权；`/ready` 可匿名访问，但若传了 Authorization 则必须正确。`/ready` 只检查数据库、Worker 心跳和 SOFA 存在性，不证明原生核心可用或某个预设可渲染。

| 方法与路径 | 成功响应 |
|---|---|
| `GET /health` | 200：ok/status/service/api_version |
| `GET /ready` | 200 或 503：ok/status/checks（database/worker/hrtf_data） |
| `GET /v1/presets` | 200：ok/api_version/engine/default_preset/presets/parameter_ranges |
| `GET /v1/user-presets` | 200：我的预设列表，含 user_preset_id/name/preset/controls/created_at/updated_at |
| `POST /v1/user-presets` | 201：保存基础预设版本和五个控件，返回保存记录 |
| `PATCH /v1/user-presets/{identity}` | 200：以 `{"name":"新名称"}` 重命名 |
| `DELETE /v1/user-presets/{identity}` | 200：`{"deleted":true}` |
| `GET /v1/user-presets/export` | 200：schema_version=1 的预设 JSON 包 |
| `POST /v1/user-presets/import` | 201：原子导入预设 JSON 包，返回新记录列表 |
| `POST /v1/assets` | 201：asset_id、文件摘要和音频元数据 |
| `POST /v1/jobs` | 202：任务状态（含幂等重放） |
| `POST /v1/previews` | 202：最多 20 秒等响度原音／效果试听任务 |
| `GET /v1/jobs/{id}` | 200：任务状态，失败任务仍为 HTTP 200，需读 status/error |
| `POST /v1/jobs/{id}/cancel` | 200：最新状态；可重复请求 |
| `GET /v1/jobs/{id}/result` | 200：音频流，Content-Length/Content-Disposition；WAV、audio/mp4 或 audio/mpeg |
| `GET /v1/jobs/{id}/original` | 200：试听的等响度原音 float32 stereo WAV |
| `GET /v1/jobs/{id}/report` | 200：完整 ConversionReport JSON |

GUI 静态文件构建后在 `/` 提供。仅本地 `aurora gui` 模式有 `/gui/session`，经回环、Host、Origin 校验返回临时会话 Key；不作为远程集成鉴权入口。

## curl 完整转换

先在环境设置 Key（与部署一致），从仓库根目录执行。下面用 Python 标准库提取字段，无需 jq。

```bash
export AURORA_SERVER=http://127.0.0.1:8000
mkdir -p outputs/api
curl --fail-with-body -sS "$AURORA_SERVER/health"
curl --fail-with-body -sS "$AURORA_SERVER/ready"
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/presets"
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  -F 'file=@data/assets/synth_voice_mono.wav' \
  "$AURORA_SERVER/v1/assets" > outputs/api/asset.json
asset_id=$(python3 -c 'import json; print(json.load(open("outputs/api/asset.json"))["asset_id"])')
python3 - "$asset_id" > outputs/api/request.json <<'PY'
import json, sys
json.dump({"api_version":"v1", "mode":"enhance", "input":{"asset_id":sys.argv[1]},
           "preset":{"id":"V2", "version":"1"}, "strength":0.4, "seed":20261016,
           "output":{"format":"wav", "sample_rate":48000, "bit_depth":24},
           "timing":{"mode":"preserve", "tail":"trim"}}, sys.stdout)
PY
# 同一次逻辑操作复用此 key 和完全相同的 request.json。
idem_key=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $idem_key" \
  --data-binary @outputs/api/request.json "$AURORA_SERVER/v1/jobs" > outputs/api/job.json
job_id=$(python3 -c 'import json; print(json.load(open("outputs/api/job.json"))["job_id"])')
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/jobs/$job_id"
# 间隔 0.5–2 秒重复查询，直到 status=succeeded；failed/canceled 立即停止。
# 确认 succeeded 后：
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/jobs/$job_id/report" -o outputs/api/report.json
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/jobs/$job_id/result" -o outputs/api/result.wav
python3 - <<'PY'
import hashlib, json
from pathlib import Path
r = json.loads(Path("outputs/api/report.json").read_text())
assert hashlib.sha256(Path("outputs/api/result.wav").read_bytes()).hexdigest() == r["output"]["sha256"]
print(r["timeline"], r["loudness_after"])
PY
```

取消：`curl --fail-with-body -sS -X POST -H "Authorization: Bearer $AURORA_API_KEY" "$AURORA_SERVER/v1/jobs/$job_id/cancel"`。完整的有界轮询、重试、恢复与下载校验见 [Python 示例](../../examples/python_client.py)。

## 上传与任务参数

输入支持 FLAC、WAV、ALAC（M4A）、AIFF、AAC、MP3。上传支持 multipart 的 `file` 字段，也支持 `Content-Type: application/octet-stream` 加 `--data-binary @FILE`；原始上传用 `X-Asset-Filename: voice.wav` 指定文件名，可附 `X-Asset-Sha256` 校验内容。文件名会清理，服务器不接收本地路径或 URL 抓取。上传响应字段为 ok/asset_id/filename/sha256/size_bytes/format/container/codec/sample_rate/channels/samples/duration_seconds/created_at/expires_at。

任务请求字段：api_version 固定 v1，mode 固定 enhance，input 必须含 asset_id 且禁止 path；preset 为 id/version，strength 0–1，seed 为 uint32。output 为 format/container/sample_rate/bit_depth/bitrate_kbps；timing 为 mode=preserve、tail=trim|preserve、fade_out_ms（默认 10）。字段默认与约束见 [CLI](cli.md) 或 schema；未知字段会拒绝。默认使用真实 Rust 路线 V 与 SADIE II D1；省略 preset 时读取 route_v.toml 的 default_preset，缺失时警告并退回该文件第一个预设。`room` 是经典路线 B，需要时显式选择。旧核心报 `CORE_ROUTE_UNSUPPORTED` 时重新构建核心并重启服务。

job 和 preview 均可传入部分或全部 `controls`，省略项从对应 TOML `[Vx.controls]` 读取。例如：

```json
{
  "input": {"asset_id": "asset_example"},
  "preset": {"id": "V3", "version": "1"},
  "controls": {"strength": 0.8, "width": 0.8, "height": 0.7, "room": 0.3, "voice_distance": 0.1}
}
```

五个键分别表示整体空间强度、声场左右展开、上方扬声器层、混响量与长度、人声远近（0 最近）。HTTP 使用归一化数值，不接受 `%` 字符串。有限越界值截断到 `[0,1]`，日志及报告 `params.control_clipping` 记录 `{control,requested,applied}`；NaN/Inf、未知控件拒绝。同名平铺参数与 `controls` 内参数不同会返回 422，应只在一处传值。

报告顶层 `controls` 和 `effective_params` 是原生路线 V 实际读回；归一化请求在 `params.controls`。经典 B 只生效 strength，其余四项列入 `ignored_controls`；桩忽略全部五项。两者 `effective_params=null`，`controls_note` 说明原因。控件及截断事件参与任务幂等摘要。

## 我的预设 JSON

`POST /v1/user-presets` 的正文为下例 `presets` 中的单项；`POST /v1/user-presets/import` 接受完整包，`GET /v1/user-presets/export` 返回相同包格式：

```json
{
  "schema_version": 1,
  "presets": [{
    "name": "夜听空间",
    "preset": {"id": "V3", "version": "1"},
    "controls": {"strength": 0.8, "width": 0.8, "height": 0.7, "room": 0.3, "voice_distance": 0.1}
  }]
}
```

仅接受支持全部五项控件的路线 V 基础预设及当前版本。名称去首尾空白，长度 1–80；每包最多 100 项。空白名称、未知键、包内重名返回 422；与数据库重名返回 409 `USER_PRESET_NAME_CONFLICT` 且整批回滚。数据库启动时迁移新表，原素材/任务保留，TTL 清理不删除个人预设。

预设管理端点使用 API 自己的 SQLite，不接受任意数据库路径。`identity` 可为记录 ID 或名称；不存在返回 404 `USER_PRESET_NOT_FOUND`。客户端创建渲染任务时发送选中记录的 `preset` 和 `controls`。本地 GUI 连接远程渲染时仍在本机管理这些记录；任务传参不要求远程数据库有相同预设 ID。

## 试听与格式

试听示例（复用已上传的 asset_id）：

```bash
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: preview-$idem_key" \
  -d "{\"asset_id\":\"$asset_id\",\"preset\":{\"id\":\"V2\",\"version\":\"1\"},\"strength\":0.4,\"seed\":20261016,\"start_seconds\":0,\"duration_seconds\":5}" \
  "$AURORA_SERVER/v1/previews"
```

导出 `output.format` 支持 `wav/flac/alac/aiff/m4a/mp3`，ALAC 的 `container` 为 `m4a`。`sample_rate` 为 `"source"`（默认）或数值 `44100/48000/96000`，`bit_depth` 为 `16/24`，WAV 另可用 `"32f"`（兼容旧数值 `32`）；有损输出归一化为 null。无损默认 24 bit，16 bit 加 TPDF 抖动。例：`"output":{"format":"flac","sample_rate":"source","bit_depth":24}`。返回的 output.sample_rate 是实际数值采样率，params.output.sample_rate 保留请求策略。ALAC 下载扩展名为 .m4a、MIME audio/mp4；FLAC 为 audio/flac，AIFF 为 audio/aiff。保留标题、艺术家、专辑和嵌入封面，WAV 使用 RIFF ID3/APIC。

内部按 48 kHz 处理，超过 24 kHz 的内容不做空间处理。服务端须使用支持 libsoxr 的 FFmpeg（可设置 AURORA_FFMPEG）；否则需要重采样的任务明确失败为 SOXR_UNAVAILABLE，不降级。MP3 最高 48 kHz、AAC 最高 96 kHz，source 超出编码器范围时任务以 INVALID_OUTPUT_PARAMETERS 失败，需改选数值采样率或无损格式。完整支持矩阵见 [快速开始](quickstart.md#格式采样率与位深)。

试听 start_seconds ≥0，duration_seconds >0 且 ≤20，起点必须小于素材时长，结尾截在素材末尾。固定输出 48 kHz float32 stereo WAV，轮询成功后 result 是效果、original 是原音；report.preview 包含原音摘要、增益、响度差、match_status 和原始时间位置。

将试听响应的 job_id 保存为 `preview_job_id`，轮询成功后分别下载：

```bash
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/jobs/$preview_job_id/original" -o outputs/api/original.wav
curl --fail-with-body -sS -H "Authorization: Bearer $AURORA_API_KEY" \
  "$AURORA_SERVER/v1/jobs/$preview_job_id/result" -o outputs/api/effect.wav
```

## 幂等、轮询与过期

`Idempotency-Key` 作用于创建 job/preview，同 key + 归一化后相同参数返回原 job；不同参数（包括不同 asset_id）返回 409。上传没有幂等保证：重试可能留下额外素材，由 TTL 清理；已知 asset_id 时直接复用。重启客户端应持久保存 key、asset_id、请求正文和 job_id。重试网络错误、429 或可重试 5xx 时保留相同请求；401/409/410/422 先修正原因。不要用新的上传 asset_id 重放旧 key。

状态为 queued/running/succeeded/failed/canceled。响应有 ok/job_id/asset_id/preview/status/cancel_requested/canceling/progress（0–1）/attempts/max_retries/created_at/started_at/finished_at/expires_at/result_metadata/error。succeeded 的 result_metadata 含 format/container/codec/sample_rate/channels/samples/duration_seconds/bit_depth/size_bytes/sha256/expires_at。failed 的 error 有 code/message/retryable/details。running 取消时 canceling=true，终态重复取消不会删除结果或改变状态。

下载前确认 succeeded；核对下载字节数和 SHA-256（result_metadata 与 report.output）。TTL 默认 24 小时：素材从上传时计，结果在成功落盘时更新期限。过期下载 result/original/report 返回 410；状态记录仍可查询，不能仅凭 status=succeeded 判断文件可用。Worker 清理保护排队／运行素材和活跃下载，数据库保留任务及幂等记录；重放过期的旧 key 不会生成新结果，需要重新上传并用新 key。

## 错误码

业务错误为 `{"ok":false,"error":{"code":"…","message":"…","exit_code":5,"retryable":false,"details":{}}}`。HTTP 层无效路径等框架错误不保证此信封。

| HTTP | 稳定业务码／处理 |
|---|---|
| 400 | MISSING_UPLOAD_FILE、EMPTY_ASSET_UPLOAD、ASSET_CHECKSUM_MISMATCH |
| 401 | UNAUTHORIZED：检查 Bearer Key |
| 404 | ASSET_NOT_FOUND、JOB_NOT_FOUND |
| 409 | IDEMPOTENCY_KEY_CONFLICT、JOB_NOT_SUCCEEDED、NOT_A_PREVIEW |
| 410 | ASSET_EXPIRED、RESULT_EXPIRED：重新创建逻辑操作 |
| 413 | ASSET_TOO_LARGE：缩小素材或调整限额 |
| 422 | INVALID_PARAMETERS、UNSUPPORTED_PRESET_VERSION、DURATION_LIMIT_EXCEEDED、PREVIEW_OUT_OF_RANGE；输入探测错误也可映射为 422 |
| 500 | 处理失败；看 error.code/retryable，勿无限重试 |
| 503 | `/ready` 返回 not_ready 和 checks；这不是通用错误信封 |

任务执行错误通常出现在状态的 error 中，例如 CORE_NOT_INSTALLED、CORE_ROUTE_UNSUPPORTED、JOB_TIMEOUT_EXCEEDED、编码失败。完整报告字段见 [CLI JSON 参考](cli.md)。
