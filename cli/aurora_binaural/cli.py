"""Command-line interface (`aurora`) for Aurora Binaural (docs/plan.md Section 9).

Key invariants:
- stdout emits strictly structured JSON.
- stderr receives all diagnostic, progress, and warning logs.
- Exit codes strictly follow Section 9:
  - 0: Success
  - 2: Parameter error
  - 3: Input problem
  - 4: Processing failure
  - 5: Remote error
  - 130: Canceled
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

from aurora_binaural.exit_codes import (
    AuroraError,
    ExitCode,
    InputAudioError,
    OperationCanceledError,
    ParameterValidationError,
    ProcessingError,
    RemoteServiceError,
)
from aurora_binaural.media import decode_to_f32le, log_stderr, measure_loudness_f32le, probe_audio
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
from aurora_binaural.pipeline import convert_audio
from aurora_binaural.timeline import (
    run_timeline_verification_suite,
    write_timeline_verification_json,
)


class _AuroraArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that routes parse errors through ParameterValidationError (ExitCode 2)
    and routes help text to stderr if needed so stdout remains strictly JSON-parseable.
    """

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
    if suffix in ("wav", "m4a", "mp3"):
        inferred = OutputFormat(suffix)

    if explicit_format is not None:
        try:
            fmt = OutputFormat(explicit_format.lower())
        except ValueError as exc:
            raise ParameterValidationError(
                f"Unsupported output format '{explicit_format}'. Choose from: wav, m4a, mp3.",
                error_code="INVALID_OUTPUT_FORMAT",
            ) from exc
        if inferred is not None and inferred != fmt:
            raise ParameterValidationError(
                f"Conflicting --format '{fmt.value}' and output extension '.{suffix}'.",
                error_code="FORMAT_EXTENSION_MISMATCH",
            )
        return fmt

    if inferred is not None:
        return inferred

    return OutputFormat.WAV


def _handle_remote_request(server_url: str, endpoint: str) -> dict[str, Any]:
    """Perform a minimal health/status request against a remote server or raise RemoteServiceError (Exit 5)."""
    if not server_url.startswith(("http://", "https://")):
        raise ParameterValidationError(
            f"Invalid --server URL '{server_url}': must start with http:// or https://",
            error_code="INVALID_SERVER_URL",
        )
    api_key = _load_api_key_from_env_or_config()
    url = server_url.rstrip("/") + "/" + endpoint.lstrip("/")
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {"ok": True}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RemoteServiceError(
            f"Failed to communicate with remote Aurora server '{server_url}': {exc}",
            error_code="REMOTE_UNREACHABLE",
            details={"server": server_url, "endpoint": endpoint},
        ) from exc


def _cmd_presets(_args: argparse.Namespace) -> int:
    presets_list = [meta.model_dump(mode="json") for meta in PRESET_CATALOG.values()]
    payload = {
        "ok": True,
        "api_version": "v1",
        "presets": presets_list,
        "parameter_ranges": {
            "strength": {"min": 0.0, "max": 1.0, "dry_wet_mixing_allowed": False},
            "seed": {"min": 0, "max": 4294967295, "default": 20261016},
            "sample_rate": [44100, 48000, 96000],
            "wav_bit_depth": [16, 24, 32],
            "lossy_bitrate_kbps": {"min": 64, "max": 512, "default": 256},
        },
    }
    _emit_json_stdout(payload)
    return int(ExitCode.OK)


def _cmd_inspect(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    log_stderr(f"Inspecting audio file: {input_path}")
    pcm_bytes, sr, ch = decode_to_f32le(input_path)
    probe_info = probe_audio(input_path, predecoded=(pcm_bytes, sr, ch))
    loudness = measure_loudness_f32le(pcm_bytes, sr, ch)
    payload = {
        "ok": True,
        "audio": probe_info.model_dump(mode="json"),
        "loudness": loudness.model_dump(mode="json"),
    }
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
    if args.server:
        # Verify remote server reachability; raise ExitCode.REMOTE_ERROR (5) if unreachable
        _handle_remote_request(args.server, "/health")
        raise RemoteServiceError(
            "Remote job submission is scheduled for P2/P3 implementation.",
            error_code="REMOTE_NOT_IMPLEMENTED_IN_P0",
            details={"server": args.server},
        )

    input_path = Path(args.input)
    output_path = Path(args.output)
    report_path = Path(args.report) if args.report else None

    fmt = _infer_output_format(output_path, args.format)

    try:
        preset_spec = PresetSpec(
            id=PresetId(args.preset),
            version=args.preset_version,
        )
        bit_depth = args.bit_depth if fmt == OutputFormat.WAV else None
        bitrate_kbps = args.bitrate if fmt != OutputFormat.WAV else None
        if fmt == OutputFormat.WAV and args.bitrate is not None:
            raise ParameterValidationError(
                "--bitrate cannot be used with uncompressed WAV output.",
                error_code="INVALID_OUTPUT_PARAMETERS",
            )

        output_spec = OutputSpec(
            format=fmt,
            sample_rate=args.sample_rate,
            bit_depth=bit_depth,
            bitrate_kbps=bitrate_kbps,
        )
        timing_spec = TimingSpec(
            mode=TimingMode(args.timing_mode),
            tail=TailPolicy(args.tail),
            fade_out_ms=args.fade_out_ms,
        )
        params = AuroraParams(
            preset=preset_spec,
            strength=args.strength,
            seed=args.seed,
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
    )
    _emit_json_stdout(report.model_dump(mode="json"))
    return int(ExitCode.OK)


def _cmd_batch(args: argparse.Namespace) -> int:
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir)
    if not in_dir.exists() or not in_dir.is_dir():
        raise InputAudioError(
            f"Batch input directory does not exist: {in_dir}",
            error_code="INPUT_DIR_NOT_FOUND",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = OutputFormat(args.format.lower())
    files = sorted(p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in (".wav", ".m4a", ".mp3", ".aac"))
    if not files:
        raise InputAudioError(
            f"No supported audio files (.wav, .m4a, .mp3, .aac) found in {in_dir}",
            error_code="EMPTY_BATCH_INPUT",
        )

    item_results: list[dict[str, Any]] = []
    failed_count = 0
    for src in files:
        dst = out_dir / f"{src.stem}.{fmt.value}"
        try:
            params = AuroraParams(
                preset=PresetSpec(id=PresetId(args.preset), version="1"),
                strength=args.strength,
                seed=args.seed,
                output=OutputSpec(
                    format=fmt,
                    sample_rate=args.sample_rate,
                    bit_depth=24 if fmt == OutputFormat.WAV else None,
                ),
            )
            rep = convert_audio(src, dst, params, overwrite=args.overwrite)
            item_results.append(
                {
                    "input": str(src),
                    "output": str(dst),
                    "ok": True,
                    "samples": rep.output.samples,
                    "sha256": rep.output.sha256,
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

    payload = {
        "ok": failed_count == 0,
        "total": len(files),
        "succeeded": len(files) - failed_count,
        "failed": failed_count,
        "results": item_results,
    }
    _emit_json_stdout(payload)
    return int(ExitCode.OK if failed_count == 0 else ExitCode.PROCESSING_ERROR)


def _cmd_job(args: argparse.Namespace) -> int:
    if not args.server:
        raise ParameterValidationError(
            "`aurora job` requires `--server <url>`.",
            error_code="MISSING_SERVER_URL",
        )
    res = _handle_remote_request(args.server, f"/v1/jobs/{args.job_id}")
    _emit_json_stdout(res)
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
    p_convert.add_argument("input", help="Input audio file path (.wav, .m4a, .mp3, .aac)")
    p_convert.add_argument("--output", "-o", required=True, help="Output audio file path (.wav, .m4a, .mp3)")
    p_convert.add_argument(
        "--preset",
        default="room",
        help="Preset identifier: room, surround, or orbit (default: room)",
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
        help="Explicit output format: wav, m4a, or mp3 (inferred from --output if omitted)",
    )
    p_convert.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        help="Output sample rate in Hz: 44100, 48000, or 96000 (default: 48000)",
    )
    p_convert.add_argument(
        "--keep-sample-rate",
        action="store_true",
        help="Preserve the input file's sample rate in output (if 44100, 48000, or 96000 Hz)",
    )
    p_convert.add_argument(
        "--bit-depth",
        type=int,
        default=24,
        help="WAV PCM bit depth: 16, 24, or 32 (default: 24)",
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
        default=5.0,
        help="Short fade-out in ms when tail=trim and reverb tail > 0 (default: 5.0)",
    )
    p_convert.add_argument(
        "--block-size",
        type=int,
        default=256,
        help="Engine processing block size in frames (default: 256)",
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
    p_convert.set_defaults(func=_cmd_convert)

    # aurora inspect
    p_inspect = subparsers.add_parser(
        "inspect",
        help="Probe audio metadata, sample count, padding, SHA-256, and BS.1770 loudness",
    )
    p_inspect.add_argument("input", help="Input audio file path")
    p_inspect.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_inspect.set_defaults(func=_cmd_inspect)

    # aurora presets
    p_presets = subparsers.add_parser(
        "presets",
        help="List available spatial presets and parameter bounds as JSON",
    )
    p_presets.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_presets.set_defaults(func=_cmd_presets)

    # aurora schema
    p_schema = subparsers.add_parser(
        "schema",
        help="Export the unified Pydantic v2 parameter JSON Schema",
    )
    p_schema.add_argument("--output", "-o", default=None, help="Optional file path to write JSON Schema")
    p_schema.set_defaults(func=_cmd_schema)

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
    p_batch.add_argument("--preset", default="room", help="Preset ID (room, surround, orbit)")
    p_batch.add_argument("--strength", type=float, default=None, help="Effect strength in [0.0, 1.0]")
    p_batch.add_argument("--seed", type=int, default=20261016, help="Deterministic PRNG seed")
    p_batch.add_argument("--format", default="wav", help="Output format: wav, m4a, or mp3")
    p_batch.add_argument("--sample-rate", type=int, default=48000, help="Output sample rate in Hz")
    p_batch.add_argument("--overwrite", action="store_true", help="Overwrite existing output files")
    p_batch.set_defaults(func=_cmd_batch)

    # aurora job
    p_job = subparsers.add_parser(
        "job",
        help="Query or manage remote asynchronous jobs",
    )
    p_job.add_argument("action", choices=["status"], help="Job action (status)")
    p_job.add_argument("job_id", help="Remote job identifier")
    p_job.add_argument("--server", required=True, help="Remote Aurora server URL")
    p_job.add_argument("--json", action="store_true", help="Output JSON (enabled by default)")
    p_job.set_defaults(func=_cmd_job)

    return parser


def run_cli(argv: Sequence[str] | None = None) -> int:
    """Execute CLI with given arguments and return numerical exit code (Section 9)."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
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
