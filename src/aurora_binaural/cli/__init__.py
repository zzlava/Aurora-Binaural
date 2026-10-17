"""Command-line interface (`aurora`) for Aurora Binaural (docs/plan.md Section 9 & P2-antigravity-01).

Key invariants:
- stdout emits strictly structured JSON.
- stderr receives all diagnostic, progress, and warning logs.
- Exit codes strictly follow Section 9:
  - 0: Success
  - 2: Parameter error (including `aurora gui` before P4)
  - 3: Input problem
  - 4: Processing failure (including strict FFmpeg < 7 rejection for M4A)
  - 5: Remote error
  - 130: Canceled (SIGINT / Ctrl-C, with temporary file cleanup)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, NoReturn

from pydantic import ValidationError

from aurora_binaural.catalog import DEFAULT_PRESET_ID
from aurora_binaural.cli.remote import RemoteAuroraClient
from aurora_binaural.exit_codes import (
    AuroraError,
    ExitCode,
    InputAudioError,
    OperationCanceledError,
    ParameterValidationError,
    ProcessingError,
)
from aurora_binaural.media import (
    check_m4a_ffmpeg_support,
    get_ffmpeg_version,
    log_stderr,
    measure_loudness_file,
    probe_audio,
)
from aurora_binaural.params import (
    PRESET_CATALOG,
    AuroraParams,
    OutputFormat,
    OutputSpec,
    PresetId,
    PresetSpec,
    TailPolicy,
    TimingMode,
    TimingSpec,
    get_params_json_schema,
    write_params_json_schema,
)
from aurora_binaural.pipeline import compare_audio, convert_audio
from aurora_binaural.timeline import (
    run_timeline_verification_suite,
    write_timeline_verification_json,
)


class _AuroraArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that routes parse errors through ParameterValidationError (ExitCode 2)."""

    def error(self, message: str) -> NoReturn:
        raise ParameterValidationError(
            f"CLI argument error: {message}",
            error_code="CLI_ARGUMENT_ERROR",
        )


def _emit_json_stdout(payload: Any) -> None:
    """Write strictly formatted JSON to stdout."""
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _load_api_key_from_env_or_config() -> str | None:
    """Load API key strictly from environment variable or config file (Section 9)."""
    env_key = os.environ.get("AURORA_API_KEY")
    if env_key:
        return env_key.strip()
    cfg_path_str = os.environ.get("AURORA_CONFIG_PATH")
    if cfg_path_str:
        cfg_path = Path(cfg_path_str)
        if cfg_path.is_file():
            try:
                cfg_data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(cfg_data, dict):
                    raw_key = cfg_data.get("api_key")
                    if isinstance(raw_key, str):
                        return raw_key.strip()
            except Exception as exc:
                raise ParameterValidationError(
                    f"Failed to parse AURORA_CONFIG_PATH '{cfg_path}': {exc}",
                    error_code="INVALID_CONFIG_FILE",
                ) from exc
    return None


def _infer_output_format(output_path: Path, explicit_format: str | None) -> OutputFormat:
    """Infer or validate OutputFormat from output file extension and optional --format flag."""
    suffix = output_path.suffix.lower().lstrip(".")
    if suffix == "aac":
        raise ParameterValidationError(
            "Raw '.aac' (ADTS) is not supported as an output container because it lacks edit-list "
            "gapless metadata. Use '.m4a' for AAC output or '.wav' for uncompressed PCM.",
            error_code="UNSUPPORTED_OUTPUT_CONTAINER",
        )

    inferred: OutputFormat | None = None
    if suffix == "aif":
        suffix = "aiff"
    if suffix in ("wav", "m4a", "mp3", "flac", "aiff", "alac"):
        inferred = OutputFormat(suffix)

    if explicit_format is not None:
        try:
            fmt = OutputFormat(explicit_format.lower())
        except ValueError as exc:
            raise ParameterValidationError(
                f"Unsupported output format '{explicit_format}'. Choose from: wav, flac, alac, aiff, m4a, mp3.",
                error_code="INVALID_OUTPUT_FORMAT",
            ) from exc
        if inferred is not None and inferred != fmt and not (inferred == OutputFormat.M4A and fmt == OutputFormat.ALAC):
            raise ParameterValidationError(
                f"Conflicting --format '{fmt.value}' and output extension '.{suffix}'.",
                error_code="FORMAT_EXTENSION_MISMATCH",
            )
        return fmt

    if inferred is not None:
        return inferred

    return OutputFormat.WAV


def _build_aurora_params_from_args(
    *,
    preset: str,
    preset_version: str = "1",
    strength: float | None = None,
    seed: int = 20261016,
    fmt: OutputFormat = OutputFormat.WAV,
    sample_rate: Literal["source", 44100, 48000, 96000] = "source",
    bit_depth: Literal[16, 24, 32, "32f"] | None = 24,
    bitrate: int | None = None,
    timing_mode: str = "preserve",
    tail: str = "trim",
    fade_out_ms: float = 10.0,
) -> AuroraParams:
    """Construct and validate `AuroraParams` from CLI arguments, raising `ParameterValidationError` on failure."""
    try:
        preset_spec = PresetSpec(
            id=PresetId(preset),
            version=preset_version,
        )
        eff_bit_depth = bit_depth if fmt in (OutputFormat.WAV, OutputFormat.FLAC, OutputFormat.ALAC, OutputFormat.AIFF) else None
        eff_bitrate = bitrate
        output_spec = OutputSpec(
            format=fmt,
            sample_rate=sample_rate,
            bit_depth=eff_bit_depth,
            bitrate_kbps=eff_bitrate,
        )
        timing_spec = TimingSpec(
            mode=TimingMode(timing_mode),
            tail=TailPolicy(tail),
            fade_out_ms=fade_out_ms,
        )
        return AuroraParams(
            preset=preset_spec,
            strength=strength,
            seed=seed,
            output=output_spec,
            timing=timing_spec,
        )
    except ValidationError as exc:
        raise ParameterValidationError(
            f"Parameter validation failed: {exc}",
            error_code="INVALID_PARAMETERS",
            details={"errors": json.loads(exc.json())},
        ) from exc
    except ValueError as exc:
        raise ParameterValidationError(
            f"Invalid parameter value: {exc}",
            error_code="INVALID_PARAMETERS",
        ) from exc


def _cmd_presets(_args: argparse.Namespace) -> int:
    presets_list = [meta.model_dump(mode="json") for meta in PRESET_CATALOG.values()]
    payload = {
        "ok": True,
        "api_version": "v1",
        "default_preset": DEFAULT_PRESET_ID,
        "presets": presets_list,
        "parameter_ranges": {
            "strength": {"min": 0.0, "max": 1.0, "dry_wet_mixing_allowed": False},
            "seed": {"min": 0, "max": 4294967295, "default": 20261016},
            "sample_rate": ["source", 44100, 48000, 96000],
            "wav_bit_depth": [16, 24, "32f"],
            "lossless_bit_depth": [16, 24],
            "output_format": [fmt.value for fmt in OutputFormat],
            "lossy_bitrate_kbps": {"min": 64, "max": 512, "default": 256},
        },
    }
    _emit_json_stdout(payload)
    return int(ExitCode.OK)


def _cmd_inspect(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    log_stderr(f"Inspecting audio file: {input_path}")
    involves_m4a = input_path.suffix.lower() in (".m4a", ".aac", ".mp4")
    m4a_warning = check_m4a_ffmpeg_support(involves_m4a=involves_m4a, strict=args.strict_ffmpeg)

    probe_info = probe_audio(input_path)
    if probe_info.codec == "aac" and not involves_m4a:
        m4a_warning = check_m4a_ffmpeg_support(involves_m4a=True, strict=args.strict_ffmpeg)

    loudness = measure_loudness_file(input_path)
    payload: dict[str, Any] = {
        "ok": True,
        "audio": probe_info.model_dump(mode="json"),
        "loudness": loudness.model_dump(mode="json"),
    }
    if m4a_warning is not None:
        payload["sync_warning"] = m4a_warning
    _emit_json_stdout(payload)
    return int(ExitCode.OK)


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.output:
        out_path = Path(args.output)
        schema = write_params_json_schema(out_path)
        log_stderr(f"Exported JSON Schema to {out_path}")
    else:
        schema = get_params_json_schema()
    _emit_json_stdout(schema)
    return int(ExitCode.OK)


def _cmd_timeline_verify(args: argparse.Namespace) -> int:
    log_stderr("Running WAV / M4A (AAC) / MP3 timeline verification at 44.1 kHz and 48 kHz...")
    if args.output:
        out_path = Path(args.output)
        result = write_timeline_verification_json(out_path)
        log_stderr(f"Wrote timeline verification results to {out_path}")
    else:
        result = run_timeline_verification_suite()
    _emit_json_stdout(result)
    return int(ExitCode.OK)


def _cmd_convert(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report) if args.report else None

    fmt = _infer_output_format(output_path, args.format)
    params = _build_aurora_params_from_args(
        preset=args.preset,
        preset_version=args.preset_version,
        strength=args.strength,
        seed=args.seed,
        fmt=fmt,
        sample_rate=args.sample_rate,
        bit_depth=args.bit_depth,
        bitrate=args.bitrate,
        timing_mode=args.timing_mode,
        tail=args.tail,
        fade_out_ms=args.fade_out_ms,
    )

    if args.server:
        api_key = _load_api_key_from_env_or_config()
        client = RemoteAuroraClient(args.server, api_key=api_key)
        remote_report = client.run_convert_workflow(
            input_path=input_path,
            output_path=output_path,
            params=params,
            overwrite=args.overwrite,
            report_path=report_path,
        )
        _emit_json_stdout(remote_report)
        return int(ExitCode.OK)

    report = convert_audio(
        input_path=input_path,
        output_path=output_path,
        params=params,
        overwrite=args.overwrite,
        report_path=report_path,
        block_size=args.block_size,
        simulated_latency_samples=args.simulated_latency,
        simulated_tail_samples=args.simulated_tail,
        keep_input_sample_rate=args.keep_sample_rate,
        strict_ffmpeg=args.strict_ffmpeg,
    )
    _emit_json_stdout(report.model_dump(mode="json"))
    return int(ExitCode.OK)


def _cmd_compare(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    try:
        fmt = OutputFormat(args.format.lower())
    except ValueError as exc:
        raise ParameterValidationError(
            f"Unsupported output format '{args.format}'. Choose from: wav, flac, alac, aiff, m4a, mp3.",
            error_code="INVALID_OUTPUT_FORMAT",
        ) from exc

    params = _build_aurora_params_from_args(
        preset=args.preset,
        preset_version=args.preset_version,
        strength=args.strength,
        seed=args.seed,
        fmt=fmt,
        sample_rate=args.sample_rate,
        bit_depth=args.bit_depth,
        bitrate=args.bitrate,
    )
    comp_report = compare_audio(
        input_path=input_path,
        output_dir=output_dir,
        params=params,
        overwrite=args.overwrite,
        block_size=args.block_size,
        strict_ffmpeg=args.strict_ffmpeg,
    )
    _emit_json_stdout(comp_report.model_dump(mode="json"))
    return int(ExitCode.OK)


def _cleanup_temp_files_in_dir(directory: Path) -> None:
    """Remove any leftover `.aurora_tmp_*` files in `directory` (e.g. on Ctrl-C during batch)."""
    if not directory.exists() or not directory.is_dir():
        return
    for p in directory.glob(".aurora_tmp_*"):
        with contextlib.suppress(OSError):
            p.unlink(missing_ok=True)


def _cmd_batch(args: argparse.Namespace) -> int:
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    if not in_dir.exists() or not in_dir.is_dir():
        raise InputAudioError(
            f"Batch input directory does not exist: {in_dir}",
            error_code="INPUT_DIR_NOT_FOUND",
        )
    try:
        fmt = OutputFormat(args.format.lower())
    except ValueError as exc:
        raise ParameterValidationError(
            f"Unsupported batch output format '{args.format}'. Choose from: wav, flac, alac, aiff, m4a, mp3.",
            error_code="INVALID_OUTPUT_FORMAT",
        ) from exc

    params_template = _build_aurora_params_from_args(
        preset=args.preset,
        strength=args.strength,
        seed=args.seed,
        fmt=fmt,
        sample_rate=args.sample_rate,
        bit_depth=args.bit_depth,
    )

    files = sorted(
        p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in (".wav", ".flac", ".m4a", ".alac", ".aiff", ".aif", ".mp3", ".aac")
    )
    if not files:
        raise InputAudioError(
            f"No supported audio files (.wav, .flac, .m4a, .aiff, .mp3, .aac) found in {in_dir}",
            error_code="EMPTY_BATCH_INPUT",
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    item_results: list[dict[str, Any]] = []
    failed_count = 0
    try:
        for src in files:
            dst = out_dir / f"{src.stem}.{params_template.output.extension}"
            try:
                rep = convert_audio(
                    src,
                    dst,
                    params_template,
                    overwrite=args.overwrite,
                    block_size=args.block_size,
                    strict_ffmpeg=args.strict_ffmpeg,
                )
                item_results.append(
                    {
                        "input": str(src),
                        "output": str(dst),
                        "ok": True,
                        "samples": rep.output.samples,
                        "sha256": rep.output.sha256,
                        "sync_within_tolerance": rep.timeline.sync_within_tolerance,
                        "sync_warning": rep.timeline.sync_warning,
                    }
                )
            except AuroraError as exc:
                failed_count += 1
                item_results.append(
                    {
                        "input": str(src),
                        "output": str(dst),
                        "ok": False,
                        "error": exc.to_dict()["error"],
                    }
                )
    finally:
        _cleanup_temp_files_in_dir(out_dir)

    payload = {
        "ok": failed_count == 0,
        "total": len(files),
        "succeeded": len(files) - failed_count,
        "failed": failed_count,
        "results": item_results,
    }
    _emit_json_stdout(payload)
    return int(ExitCode.OK if failed_count == 0 else ExitCode.PROCESSING_ERROR)


def _cmd_gui(args: argparse.Namespace) -> int:
    from aurora_binaural.gui import run_gui

    return run_gui(port=args.port, open_browser=not args.no_browser)


def _cmd_job(args: argparse.Namespace) -> int:
    if not args.server:
        raise ParameterValidationError(
            "`aurora job` requires `--server <url>`.",
            error_code="MISSING_SERVER_URL",
        )
    api_key = _load_api_key_from_env_or_config()
    client = RemoteAuroraClient(args.server, api_key=api_key)
    res = client.cancel_job(args.job_id) if args.action == "cancel" else client.get_job_status(args.job_id)
    _emit_json_stdout(res)
    return int(ExitCode.OK)


def _cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from aurora_binaural.api import create_app
    from aurora_binaural.config import ServiceConfig

    cfg = ServiceConfig.from_env()
    host = args.host or cfg.host
    port = args.port or cfg.port
    cfg = ServiceConfig(
        api_key=cfg.api_key,
        host=host,
        port=port,
        data_dir=cfg.data_dir,
        db_path=cfg.db_path,
        hrtf_dir=cfg.hrtf_dir,
        max_upload_bytes=cfg.max_upload_bytes,
        max_duration_sec=cfg.max_duration_sec,
        job_timeout_sec=cfg.job_timeout_sec,
        retention_sec=cfg.retention_sec,
        worker_concurrency=cfg.worker_concurrency,
        lease_duration_sec=cfg.lease_duration_sec,
        heartbeat_interval_sec=cfg.heartbeat_interval_sec,
        cleanup_interval_sec=cfg.cleanup_interval_sec,
        worker_poll_interval_sec=cfg.worker_poll_interval_sec,
        max_retries=cfg.max_retries,
        worker_block_delay_ms=cfg.worker_block_delay_ms,
        strict_ffmpeg=cfg.strict_ffmpeg,
        cors_origins=cfg.cors_origins,
    )
    app = create_app(cfg)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return int(ExitCode.OK)


def _cmd_worker(_args: argparse.Namespace) -> int:
    import signal
    from types import FrameType

    from aurora_binaural.config import ServiceConfig
    from aurora_binaural.worker import AuroraWorker

    cfg = ServiceConfig.from_env()
    worker = AuroraWorker(cfg)

    def _handle_sig(_signum: int, _frame: FrameType | None) -> None:
        worker.stop(timeout=2.0)

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)
    worker.serve_forever()
    return int(ExitCode.OK)


def _cmd_openapi(args: argparse.Namespace) -> int:
    from aurora_binaural.api import export_openapi_schema

    out_path = Path(args.output) if args.output else None
    schema = export_openapi_schema(out_path)
    if out_path is not None:
        log_stderr(f"Exported OpenAPI schema to {out_path}")
    _emit_json_stdout(schema)
    return int(ExitCode.OK)


def build_parser() -> argparse.ArgumentParser:
    """Construct the `aurora` CLI argument parser."""
    parser = _AuroraArgumentParser(
        prog="aurora",
        description="Aurora Binaural CLI — Spatial binaural audio processor and verifier",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # aurora convert
    p_convert = subparsers.add_parser(
        "convert",
        help="Convert a mono/stereo audio file through the binaural pipeline and verify timeline sync",
    )
    p_convert.add_argument("input", help="Input audio file path (.wav, .flac, .m4a, .aiff, .mp3, .aac)")
    p_convert.add_argument("--output", "-o", required=True, help="Output audio file path (.wav, .flac, .m4a, .aiff, .mp3)")
    p_convert.add_argument(
        "--preset",
        default=DEFAULT_PRESET_ID,
        help="Preset identifier from TOML (default: route_v.toml; room is classic route B)",
    )
    p_convert.add_argument("--preset-version", default="1", help="Preset version string (default: 1)")
    p_convert.add_argument(
        "--strength",
        type=float,
        default=None,
        help="Perceptual spatial effect strength in [0.0, 1.0] (default: preset default)",
    )
    p_convert.add_argument(
        "--seed",
        type=int,
        default=20261016,
        help="Deterministic PRNG seed (default: 20261016)",
    )
    p_convert.add_argument(
        "--format",
        default=None,
        help="Explicit output format: wav, flac, alac, aiff, m4a, or mp3 (inferred from --output if omitted)",
    )
    p_convert.add_argument(
        "--sample-rate",
        type=lambda value: "source" if value == "source" else int(value),
        default="source",
        help="Output sample rate: source (default), 44100, 48000 or 96000 Hz",
    )
    p_convert.add_argument(
        "--keep-sample-rate",
        action="store_true",
        help="Alias for --sample-rate source",
    )
    p_convert.add_argument(
        "--bit-depth",
        type=lambda value: "32f" if value == "32f" else int(value),
        default=24,
        help="Lossless bit depth: 16/24; WAV also accepts 32f (legacy 32); default 24",
    )
    p_convert.add_argument(
        "--bitrate",
        type=int,
        default=None,
        help="Bitrate in kbps for M4A/MP3 outputs in [64, 512] (default: 256)",
    )
    p_convert.add_argument(
        "--timing-mode",
        default="preserve",
        help="Timeline sync mode: preserve (default: preserve)",
    )
    p_convert.add_argument(
        "--tail",
        default="trim",
        help="Reverb tail handling policy: trim or preserve (default: trim)",
    )
    p_convert.add_argument(
        "--fade-out-ms",
        type=float,
        default=10.0,
        help="Short fade-out in ms when tail=trim (default: 10.0)",
    )
    p_convert.add_argument(
        "--block-size",
        type=int,
        default=1024,
        help="Engine processing block size in frames (default: 1024)",
    )
    p_convert.add_argument(
        "--simulated-latency",
        type=int,
        default=0,
        help="Simulated engine lookahead latency in samples for testing compensation (default: 0)",
    )
    p_convert.add_argument(
        "--simulated-tail",
        type=int,
        default=0,
        help="Simulated engine reverb tail in samples for testing tail policy (default: 0)",
    )
    p_convert.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting an existing output file (default: False)",
    )
    p_convert.add_argument(
        "--report",
        default=None,
        help="Optional file path to atomically write the JSON conversion report",
    )
    p_convert.add_argument(
        "--server",
        default=None,
        help="Optional remote Aurora API server URL (reads AURORA_API_KEY from environment)",
    )
    p_convert.add_argument(
        "--strict-ffmpeg",
        action="store_true",
        help="Reject M4A/AAC processing with exit code 4 if FFmpeg version is older than 7.0",
    )
    p_convert.set_defaults(func=_cmd_convert)

    # aurora compare
    p_compare = subparsers.add_parser(
        "compare",
        help="Generate BS.1770 equal-loudness matched dry reference and spatial effect files",
    )
    p_compare.add_argument("input", help="Input audio file path (.wav, .flac, .m4a, .aiff, .mp3, .aac)")
    p_compare.add_argument("--output-dir", required=True, help="Directory for loudness-matched dry and effect files")
    p_compare.add_argument("--preset", default=DEFAULT_PRESET_ID, help="Preset identifier from TOML (default: route_v.toml; room is classic route B)")
    p_compare.add_argument("--preset-version", default="1", help="Preset version string (default: 1)")
    p_compare.add_argument("--strength", type=float, default=None, help="Effect strength in [0.0, 1.0]")
    p_compare.add_argument("--seed", type=int, default=20261016, help="Deterministic PRNG seed")
    p_compare.add_argument("--format", default="wav", help="Output format: wav, flac, alac, aiff, m4a, or mp3 (default: wav)")
    p_compare.add_argument("--sample-rate", type=lambda v: "source" if v == "source" else int(v), default="source", help="source (default), 44100, 48000, 96000")
    p_compare.add_argument("--bit-depth", type=lambda v: "32f" if v == "32f" else int(v), default=24, help="16/24, WAV also 32f; default 24")
    p_compare.add_argument("--bitrate", type=int, default=None, help="Lossy bitrate in kbps (default: 256)")
    p_compare.add_argument("--block-size", type=int, default=1024, help="Engine block size in frames (default: 1024)")
    p_compare.add_argument("--overwrite", action="store_true", help="Allow overwriting existing output files")
    p_compare.add_argument(
        "--strict-ffmpeg",
        action="store_true",
        help="Reject M4A/AAC processing with exit code 4 if FFmpeg version is older than 7.0",
    )
    p_compare.set_defaults(func=_cmd_compare)

    # aurora inspect
    p_inspect = subparsers.add_parser(
        "inspect",
        help="Probe audio metadata, sample count, padding, SHA-256, and BS.1770 loudness",
    )
    p_inspect.add_argument("input", help="Input audio file path")
    p_inspect.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_inspect.add_argument(
        "--strict-ffmpeg",
        action="store_true",
        help="Reject M4A/AAC inspection with exit code 4 if FFmpeg version is older than 7.0",
    )
    p_inspect.set_defaults(func=_cmd_inspect)

    # aurora presets
    p_presets = subparsers.add_parser(
        "presets",
        help="List available spatial presets and parameter bounds as JSON",
    )
    p_presets.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_presets.set_defaults(func=_cmd_presets)

    # aurora gui
    p_gui = subparsers.add_parser(
        "gui",
        help="Launch local Web GUI, API and Worker on 127.0.0.1",
    )
    p_gui.add_argument("--port", type=int, default=0, help="Loopback port; 0 selects a free port")
    p_gui.add_argument("--no-browser", action="store_true", help="Run without opening a browser")
    p_gui.set_defaults(func=_cmd_gui)

    # aurora schema
    p_schema = subparsers.add_parser(
        "schema",
        help="Export the unified Pydantic v2 parameter JSON Schema",
    )
    p_schema.add_argument("--output", "-o", default=None, help="Optional file path to write JSON Schema")
    p_schema.set_defaults(func=_cmd_schema)

    # aurora openapi
    p_openapi = subparsers.add_parser(
        "openapi",
        help="Export the FastAPI OpenAPI 3.1 JSON specification",
    )
    p_openapi.add_argument("--output", "-o", default=None, help="Optional file path to write OpenAPI JSON")
    p_openapi.set_defaults(func=_cmd_openapi)

    # aurora timeline-verify
    p_timeline = subparsers.add_parser(
        "timeline-verify",
        help="Run WAV / M4A (AAC) / MP3 timeline and priming/padding verification at 44.1 kHz & 48 kHz",
    )
    p_timeline.add_argument(
        "--output",
        "-o",
        default=None,
        help="Optional file path to write timeline verification JSON",
    )
    p_timeline.set_defaults(func=_cmd_timeline_verify)

    # aurora batch
    p_batch = subparsers.add_parser(
        "batch",
        help="Batch process a directory of audio files",
    )
    p_batch.add_argument("input_dir", help="Input directory containing audio files")
    p_batch.add_argument("--output-dir", required=True, help="Output directory for exported files")
    p_batch.add_argument("--preset", default=DEFAULT_PRESET_ID, help="Preset ID from TOML (default from route_v.toml)")
    p_batch.add_argument("--strength", type=float, default=None, help="Effect strength in [0.0, 1.0]")
    p_batch.add_argument("--seed", type=int, default=20261016, help="Deterministic PRNG seed")
    p_batch.add_argument("--format", default="wav", help="Output format: wav, flac, alac, aiff, m4a, or mp3")
    p_batch.add_argument("--sample-rate", type=lambda v: "source" if v == "source" else int(v), default="source", help="source (default), 44100, 48000, 96000")
    p_batch.add_argument("--bit-depth", type=lambda v: "32f" if v == "32f" else int(v), default=24, help="16/24, WAV also 32f; default 24")
    p_batch.add_argument("--block-size", type=int, default=1024, help="Engine block size in frames (default: 1024)")
    p_batch.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    p_batch.add_argument(
        "--strict-ffmpeg",
        action="store_true",
        help="Reject M4A/AAC processing with exit code 4 if FFmpeg version is older than 7.0",
    )
    p_batch.set_defaults(func=_cmd_batch)

    # aurora job
    p_job = subparsers.add_parser(
        "job",
        help="Query or manage remote asynchronous jobs",
    )
    p_job.add_argument("action", choices=["status", "cancel"], help="Job action (status, cancel)")
    p_job.add_argument("job_id", help="Remote job identifier")
    p_job.add_argument("--server", required=True, help="Remote Aurora server URL")
    p_job.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_job.set_defaults(func=_cmd_job)

    # aurora api
    p_api = subparsers.add_parser(
        "api",
        help="Start the Aurora Binaural FastAPI HTTP server",
    )
    p_api.add_argument("--host", default=None, help="Bind host (defaults to AURORA_HOST or 127.0.0.1)")
    p_api.add_argument("--port", type=int, default=None, help="Bind port (defaults to AURORA_PORT or 8000)")
    p_api.set_defaults(func=_cmd_api)

    # aurora worker
    p_worker = subparsers.add_parser(
        "worker",
        help="Start the Aurora Binaural persistent background Worker",
    )
    p_worker.set_defaults(func=_cmd_worker)

    return parser


def _startup_ffmpeg_probe(command: str | None) -> None:
    """Detect FFmpeg version on startup for local media commands (F-AG-1)."""
    if command in ("convert", "inspect", "compare", "batch", "timeline-verify"):
        try:
            ver = get_ffmpeg_version()
            if not ver.meets_m4a_requirement:
                log_stderr(
                    f"NOTICE: Detected FFmpeg {ver.major}.{ver.minor}.{ver.patch} (< 7.0). "
                    "M4A/AAC processing will emit degradation warnings (or fail with exit code 4 under --strict-ffmpeg)."
                )
        except AuroraError:
            # Will be raised if local ffmpeg is actually invoked (not needed for --server remote mode)
            pass


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Execute CLI with given arguments and return numerical exit code (Section 9)."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if not getattr(args, "server", None):
            _startup_ffmpeg_probe(getattr(args, "command", None))
        handler = getattr(args, "func", None)
        if handler is None:
            raise ParameterValidationError("No subcommand specified.")
        return int(handler(args))
    except KeyboardInterrupt:
        err = OperationCanceledError("Operation interrupted by user (SIGINT).")
        log_stderr(err.message)
        _emit_json_stdout(err.to_dict())
        return int(ExitCode.CANCELED)
    except AuroraError as exc:
        log_stderr(f"ERROR [{exc.error_code}] (exit {int(exc.exit_code)}): {exc.message}")
        _emit_json_stdout(exc.to_dict())
        return int(exc.exit_code)
    except Exception as exc:
        internal_err = ProcessingError(f"Unexpected internal error: {exc}", error_code="INTERNAL_ERROR")
        log_stderr(f"ERROR [INTERNAL_ERROR] (exit 4): {exc}")
        _emit_json_stdout(internal_err.to_dict())
        return int(ExitCode.PROCESSING_ERROR)


def main() -> NoReturn:
    """Console script entry point for `aurora`."""
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
