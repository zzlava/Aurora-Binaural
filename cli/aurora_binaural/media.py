"""FFmpeg / FFprobe media operations, BS.1770 loudness analysis, and atomic file delivery.

Implements docs/plan.md Section 8, 9, 10:
- Probe audio format, container, codec, sample rate, channels, padding, SHA-256, and decoded sample count.
- Decode audio to 32-bit floating-point PCM (`f32le`).
- Measure BS.1770 integrated loudness (LUFS), LRA, and True Peak (dBTP) via `ffmpeg -af ebur128=peak=true`.
- Encode audio to temporary file and atomically commit only after readback verification passes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from array import array
from pathlib import Path

from aurora_binaural.exit_codes import InputAudioError, ProcessingError
from aurora_binaural.params import OutputFormat, OutputSpec
from aurora_binaural.report import AudioProbeInfo, LoudnessMetrics

_EBUR128_I_RE = re.compile(r"Integrated loudness:\s+I:\s+([-\d.]+|-inf|nan)\s+LUFS", re.MULTILINE)
_EBUR128_LRA_RE = re.compile(r"Loudness range:\s+LRA:\s+([-\d.]+|-inf|nan)\s+LU", re.MULTILINE)
_EBUR128_TPK_RE = re.compile(r"True peak:\s+Peak:\s+([-\d.]+|-inf|nan)\s+dBFS", re.MULTILINE)


def log_stderr(message: str) -> None:
    """Write structured diagnostic/progress log line strictly to stderr (Section 9)."""
    sys.stderr.write(f"[aurora] {message}\n")
    sys.stderr.flush()


def ensure_ffmpeg_available() -> tuple[str, str]:
    """Ensure both `ffmpeg` and `ffprobe` binaries exist on PATH."""
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        raise ProcessingError(
            "Required system binaries 'ffmpeg' and/or 'ffprobe' were not found on PATH.",
            error_code="FFMPEG_NOT_FOUND",
        )
    return ffmpeg_bin, ffprobe_bin


def compute_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def decode_to_f32le(
    path: Path,
    *,
    target_sample_rate: int | None = None,
    ignore_gapless: bool = False,
) -> tuple[bytes, int, int]:
    """Decode an audio file to interleaved 32-bit float little-endian PCM (`f32le`).

    Args:
        path: Input audio file path.
        target_sample_rate: Optional output sample rate (uses high-precision resampling if different).
        ignore_gapless: If True, disables container edit-list / Xing TOC gapless trimming so raw
            encoder priming and trailing padding samples can be measured directly.

    Returns:
        (pcm_f32le_bytes, sample_rate, channels)
    """
    ffmpeg_bin, ffprobe_bin = ensure_ffmpeg_available()
    if not path.exists():
        raise InputAudioError(f"Input file does not exist: {path}", error_code="INPUT_NOT_FOUND")
    if not path.is_file():
        raise InputAudioError(f"Input path is not a regular file: {path}", error_code="INPUT_NOT_A_FILE")

    probe_cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels,codec_name,duration_ts",
        "-show_entries",
        "format=format_name",
        "-of",
        "json",
        str(path),
    ]
    probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
    if probe_res.returncode != 0:
        raise InputAudioError(
            f"Failed to probe input audio '{path.name}': {probe_res.stderr.strip()}",
            error_code="CORRUPT_OR_UNSUPPORTED_INPUT",
        )

    try:
        probe_data = json.loads(probe_res.stdout)
        streams = probe_data.get("streams", [])
    except json.JSONDecodeError as exc:
        raise InputAudioError(
            f"Invalid ffprobe output for '{path.name}': {exc}",
            error_code="CORRUPT_OR_UNSUPPORTED_INPUT",
        ) from exc

    if not streams:
        raise InputAudioError(
            f"No audio stream found in '{path.name}'.",
            error_code="NO_AUDIO_STREAM",
        )

    stream = streams[0]
    in_sr = int(stream.get("sample_rate", 0))
    in_ch = int(stream.get("channels", 0))
    if in_sr <= 0 or in_ch <= 0:
        raise InputAudioError(
            f"Invalid audio stream parameters in '{path.name}' (sr={in_sr}, channels={in_ch}).",
            error_code="INVALID_AUDIO_STREAM",
        )
    if in_ch not in (1, 2):
        raise InputAudioError(
            f"Unsupported channel count {in_ch} in '{path.name}'. V1 supports mono (1) or stereo (2).",
            error_code="UNSUPPORTED_CHANNELS",
        )

    out_sr = target_sample_rate if target_sample_rate is not None else in_sr
    codec_name = str(stream.get("codec_name", "")).lower()

    demux_flags: list[str] = []
    if ignore_gapless:
        suffix = path.suffix.lower()
        if suffix in (".m4a", ".mp4", ".mov") or codec_name == "aac":
            if suffix != ".aac":
                demux_flags.extend(["-ignore_editlist", "1"])
        elif suffix == ".mp3" or codec_name == "mp3":
            demux_flags.extend(["-flags2", "+skip_manual"])

    decode_cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        *demux_flags,
        "-i",
        str(path),
        "-vn",
        "-sn",
        "-dn",
        "-ar",
        str(out_sr),
        "-ac",
        str(in_ch),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    dec_res = subprocess.run(decode_cmd, capture_output=True, check=False)
    if dec_res.returncode != 0:
        err_msg = dec_res.stderr.decode("utf-8", errors="replace").strip()
        raise InputAudioError(
            f"Failed to decode audio '{path.name}': {err_msg}",
            error_code="DECODE_FAILED",
        )

    pcm_bytes = dec_res.stdout
    frame_bytes = 4 * in_ch
    if len(pcm_bytes) == 0 or len(pcm_bytes) % frame_bytes != 0:
        raise InputAudioError(
            f"Decoded PCM buffer from '{path.name}' is empty or misaligned ({len(pcm_bytes)} bytes).",
            error_code="EMPTY_OR_CORRUPT_AUDIO",
        )

    floats = array("f")
    floats.frombytes(pcm_bytes)
    for v in floats:
        if not math.isfinite(v):
            raise ProcessingError(
                f"Decoded PCM from '{path.name}' contains non-finite (NaN/Inf) samples.",
                error_code="NON_FINITE_SAMPLES",
            )

    return pcm_bytes, out_sr, in_ch


def probe_audio(path: Path, *, predecoded: tuple[bytes, int, int] | None = None) -> AudioProbeInfo:
    """Inspect an audio file and return complete `AudioProbeInfo` with exact decoded sample count."""
    _, ffprobe_bin = ensure_ffmpeg_available()
    if not path.exists():
        raise InputAudioError(f"Audio file not found: {path}", error_code="INPUT_NOT_FOUND")
    if not path.is_file():
        raise InputAudioError(f"Path is not a file: {path}", error_code="INPUT_NOT_A_FILE")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        (
            "stream=codec_name,sample_rate,channels,channel_layout,"
            "bits_per_raw_sample,bits_per_sample,initial_padding,trailing_padding"
        ),
        "-show_entries",
        "format=format_name",
        "-of",
        "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise InputAudioError(
            f"ffprobe failed on '{path.name}': {res.stderr.strip()}",
            error_code="CORRUPT_OR_UNSUPPORTED_INPUT",
        )

    try:
        info = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise InputAudioError(
            f"Malformed ffprobe JSON for '{path.name}': {exc}",
            error_code="CORRUPT_OR_UNSUPPORTED_INPUT",
        ) from exc

    streams = info.get("streams", [])
    if not streams:
        raise InputAudioError(f"No audio stream found in '{path.name}'.", error_code="NO_AUDIO_STREAM")

    stream = streams[0]
    fmt_info = info.get("format", {})

    if predecoded is not None:
        pcm_bytes, sr, ch = predecoded
    else:
        pcm_bytes, sr, ch = decode_to_f32le(path)

    samples = len(pcm_bytes) // (4 * ch)
    duration = samples / sr if sr > 0 else 0.0

    codec = str(stream.get("codec_name", "unknown"))
    container = str(fmt_info.get("format_name", "unknown"))

    # Normalize user-facing format label (separate from container per Section 4.1)
    if codec.startswith("pcm_"):
        fmt_label = "wav"
    elif codec == "aac":
        fmt_label = "aac"
    elif codec == "mp3":
        fmt_label = "mp3"
    else:
        fmt_label = codec

    raw_bits = stream.get("bits_per_raw_sample") or stream.get("bits_per_sample")
    bit_depth: int | None = None
    if raw_bits is not None:
        try:
            parsed_bits = int(raw_bits)
            if parsed_bits > 0:
                bit_depth = parsed_bits
        except (TypeError, ValueError):
            bit_depth = None

    init_pad = int(stream.get("initial_padding") or 0)
    trail_pad = int(stream.get("trailing_padding") or 0)

    return AudioProbeInfo(
        path=str(path),
        sha256=compute_sha256(path),
        format=fmt_label,
        container=container,
        codec=codec,
        sample_rate=sr,
        channels=ch,
        channel_layout=stream.get("channel_layout"),
        samples=samples,
        duration_seconds=round(duration, 9),
        bit_depth=bit_depth,
        size_bytes=path.stat().st_size,
        initial_padding=init_pad,
        trailing_padding=trail_pad,
    )


def measure_loudness_f32le(pcm_f32le: bytes, sample_rate: int, channels: int) -> LoudnessMetrics:
    """Measure ITU-R BS.1770-4 integrated loudness, LRA, and True Peak via ffmpeg ebur128."""
    ffmpeg_bin, _ = ensure_ffmpeg_available()
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-nostats",
        "-hide_banner",
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd, input=pcm_f32le, capture_output=True, check=False)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(f"BS.1770 loudness measurement failed: {err}", error_code="LOUDNESS_FAILED")

    stderr_text = res.stderr.decode("utf-8", errors="replace")

    i_match = _EBUR128_I_RE.search(stderr_text)
    lra_match = _EBUR128_LRA_RE.search(stderr_text)
    tpk_match = _EBUR128_TPK_RE.search(stderr_text)

    if not i_match or not tpk_match:
        raise ProcessingError(
            "Could not parse BS.1770 ebur128 summary from ffmpeg output.",
            error_code="LOUDNESS_PARSE_ERROR",
        )

    raw_i = float(i_match.group(1))
    raw_lra = float(lra_match.group(1)) if lra_match else 0.0
    raw_tpk = float(tpk_match.group(1))

    is_silent = (not math.isfinite(raw_tpk)) or (not math.isfinite(raw_i)) or raw_i <= -70.0
    integrated_lufs: float | None = None if (not math.isfinite(raw_i) or is_silent) else round(raw_i, 2)
    true_peak_dbtp: float | None = None if not math.isfinite(raw_tpk) else round(raw_tpk, 2)
    lra_lu: float = 0.0 if not math.isfinite(raw_lra) else round(max(0.0, raw_lra), 2)

    return LoudnessMetrics(
        integrated_lufs=integrated_lufs,
        loudness_range_lu=lra_lu,
        true_peak_dbtp=true_peak_dbtp,
        is_silent=is_silent,
    )


def encode_f32le_to_path(
    pcm_f32le: bytes,
    *,
    sample_rate: int,
    channels: int,
    output_spec: OutputSpec,
    target_path: Path,
) -> None:
    """Encode raw interleaved float32le PCM to `target_path` using deterministic FFmpeg flags."""
    ffmpeg_bin, _ = ensure_ffmpeg_available()

    codec_args: list[str] = ["-c:a", output_spec.codec_name]
    if output_spec.format == OutputFormat.WAV:
        # Uncompressed PCM in RIFF WAV container
        codec_args.extend(["-f", "wav"])
    elif output_spec.format == OutputFormat.M4A:
        # AAC-LC inside MP4/M4A container with faststart and explicit editlist support
        bitrate = f"{output_spec.bitrate_kbps or 256}k"
        codec_args.extend(["-b:a", bitrate, "-movflags", "+faststart", "-f", "ipod"])
    elif output_spec.format == OutputFormat.MP3:
        # LAME MP3 with Xing/Info gapless header enabled (-write_xing 1)
        bitrate = f"{output_spec.bitrate_kbps or 256}k"
        codec_args.extend(["-b:a", bitrate, "-write_xing", "1", "-f", "mp3"])

    cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "f32le",
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        "-i",
        "pipe:0",
        "-ar",
        str(output_spec.sample_rate),
        "-ac",
        str(channels),
        *codec_args,
        str(target_path),
    ]
    res = subprocess.run(cmd, input=pcm_f32le, capture_output=True, check=False)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(
            f"FFmpeg encoding to {output_spec.format.value} failed: {err}",
            error_code="ENCODE_FAILED",
        )


def atomic_write_bytes(target_path: Path, data: bytes, *, overwrite: bool = False) -> None:
    """Atomically write bytes to `target_path`, respecting `overwrite` protection."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not overwrite:
        raise InputAudioError(
            f"Output file already exists and overwrite is disabled: {target_path}",
            error_code="OUTPUT_ALREADY_EXISTS",
        )

    tmp_path = target_path.parent / f".aurora_tmp_{uuid.uuid4().hex}_{target_path.name}"
    try:
        with tmp_path.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if target_path.exists() and not overwrite:
            raise InputAudioError(
                f"Output file already exists and overwrite is disabled: {target_path}",
                error_code="OUTPUT_ALREADY_EXISTS",
            )
        os.replace(tmp_path, target_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
