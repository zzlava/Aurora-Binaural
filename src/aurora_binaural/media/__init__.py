"""FFmpeg / FFprobe streaming media operations, BS.1770 loudness analysis, version checks, and atomic delivery.

Implements docs/plan.md Sections 8, 9, 10 and P2 tasks:
- Startup/runtime FFmpeg version check (F-AG-1 & F-AG-2): detects FFmpeg < 7 and emits explicit error or
  degradation warning when handling M4A/AAC inputs or outputs.
- Streaming float32le decode (`stream_decode_f32le`) and streaming encode (`StreamingF32LeEncoder`) via
  FFmpeg pipes so 60-minute 48 kHz stereo files stay well under 500 MB peak RAM.
- Probe audio format, container, codec, sample rate, channels, padding, SHA-256, and decoded sample count.
- Measure BS.1770 integrated loudness (LUFS), LRA, and True Peak (dBTP) via `ffmpeg -af ebur128=peak=true`.
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
from collections.abc import Generator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from aurora_binaural.exit_codes import InputAudioError, ProcessingError
from aurora_binaural.params import OutputFormat, OutputSpec
from aurora_binaural.report import AudioProbeInfo, LoudnessMetrics

_EBUR128_I_RE = re.compile(r"Integrated loudness:\s+I:\s+([-\d.]+|-inf|nan)\s+LUFS", re.MULTILINE)
_EBUR128_LRA_RE = re.compile(r"Loudness range:\s+LRA:\s+([-\d.]+|-inf|nan)\s+LU", re.MULTILINE)
_EBUR128_TPK_RE = re.compile(r"True peak:\s+Peak:\s+([-\d.]+|-inf|nan)\s+dBFS", re.MULTILINE)
_FFMPEG_VER_RE = re.compile(r"ffmpeg version\s+(?:n|N-)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE)

MINIMUM_M4A_FFMPEG_MAJOR = 7


@dataclass(frozen=True)
class FFmpegVersionInfo:
    """Parsed FFmpeg version metadata (F-AG-1)."""

    major: int
    minor: int
    patch: int
    raw_line: str

    @property
    def meets_m4a_requirement(self) -> bool:
        """True if FFmpeg major version is >= 7 (accurate MP4/M4A edit-list trailing packet trimming)."""
        return self.major >= MINIMUM_M4A_FFMPEG_MAJOR


def log_stderr(message: str) -> None:
    """Write structured diagnostic/progress log line strictly to stderr (Section 9)."""
    sys.stderr.write(f"[aurora] {message}\n")
    sys.stderr.flush()


def ensure_ffmpeg_available() -> tuple[str, str]:
    """Ensure both `ffmpeg` and `ffprobe` binaries exist on PATH."""
    ffmpeg_bin = shutil.which(os.environ.get("AURORA_FFMPEG", "ffmpeg"))
    ffprobe_bin = shutil.which(os.environ.get("AURORA_FFPROBE", "ffprobe"))
    if not ffmpeg_bin or not ffprobe_bin:
        raise ProcessingError(
            "Required system binaries 'ffmpeg' and/or 'ffprobe' were not found on PATH.",
            error_code="FFMPEG_NOT_FOUND",
        )
    return ffmpeg_bin, ffprobe_bin


@lru_cache(maxsize=8)
def require_soxr(ffmpeg_bin: str) -> None:
    """Exercise the resampler: help can list soxr even when it was not compiled in."""
    result = subprocess.run(
        [ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.01",
         "-af", "aresample=48000:resampler=soxr:precision=28", "-f", "null", "-"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise ProcessingError(
            "This FFmpeg cannot run soxr VHQ. Install an FFmpeg build with --enable-libsoxr and set "
            "AURORA_FFMPEG to its binary (Homebrew: /opt/homebrew/opt/ffmpeg-full/bin/ffmpeg). "
            "No lower-quality resampler was substituted.", error_code="SOXR_UNAVAILABLE",
        )


def _cover_stream(path: Path) -> int | None:
    _, ffprobe = ensure_ffmpeg_available()
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True)
    for stream in json.loads(result.stdout).get("streams", []):
        if stream.get("disposition", {}).get("attached_pic"):
            return int(stream["index"])
    return None


def preserve_wav_cover(source: Path, target: Path) -> None:
    """RIFF WAVE carries artwork in an ID3 APIC chunk (FFmpeg's WAV muxer cannot write it)."""
    index = _cover_stream(source)
    if index is None:
        return
    ffmpeg, _ = ensure_ffmpeg_available()
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(source), "-map", f"0:{index}",
                             "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1"],
                            capture_output=True, check=False)
    if result.returncode:
        raise ProcessingError("Could not preserve source artwork in WAV.", error_code="METADATA_FAILED")
    payload = b"\x00image/png\x00\x03\x00" + result.stdout
    frame = b"APIC" + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload
    size = len(frame)
    synchsafe = bytes((size >> shift) & 0x7f for shift in (21, 14, 7, 0))
    tag = b"ID3\x03\x00\x00" + synchsafe + frame
    chunk = b"id3 " + len(tag).to_bytes(4, "little") + tag + (b"\x00" if len(tag) % 2 else b"")
    with target.open("r+b") as output:
        kind = output.read(4)
        output.seek(0, 2)
        output.write(chunk)
        riff_size = output.tell() - 8
        if kind == b"RF64":
            # FFmpeg's RF64 header begins with a ds64 chunk at byte 12.
            output.seek(20)
            output.write(riff_size.to_bytes(8, "little"))
        else:
            output.seek(4)
            output.write(riff_size.to_bytes(4, "little"))


def parse_ffmpeg_version_line(version_line: str) -> FFmpegVersionInfo:
    """Parse the first line of `ffmpeg -version` into `FFmpegVersionInfo`."""
    clean = version_line.strip()
    m = _FFMPEG_VER_RE.search(clean)
    if not m:
        return FFmpegVersionInfo(major=0, minor=0, patch=0, raw_line=clean)
    major = int(m.group(1))
    minor = int(m.group(2)) if m.group(2) is not None else 0
    patch = int(m.group(3)) if m.group(3) is not None else 0
    return FFmpegVersionInfo(major=major, minor=minor, patch=patch, raw_line=clean)


def get_ffmpeg_version() -> FFmpegVersionInfo:
    """Detect installed `ffmpeg` version at startup/runtime (F-AG-1)."""
    sim_ver = os.environ.get("AURORA_SIMULATE_FFMPEG_VERSION")
    if sim_ver:
        return parse_ffmpeg_version_line(f"ffmpeg version {sim_ver}")

    ffmpeg_bin, _ = ensure_ffmpeg_available()
    res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, check=False)
    first_line = res.stdout.splitlines()[0] if res.stdout else "unknown"
    return parse_ffmpeg_version_line(first_line)


def check_m4a_ffmpeg_support(*, involves_m4a: bool, strict: bool = False) -> str | None:
    """Check FFmpeg version when processing M4A/AAC input or output (F-AG-1 & F-AG-2).

    If FFmpeg major version < 7 and `involves_m4a` is True:
    - When `strict=True` or `AURORA_STRICT_FFMPEG=1`: raises `ProcessingError` (ExitCode.PROCESSING_ERROR = 4)
      with `error_code="FFMPEG_VERSION_TOO_OLD"`.
    - Otherwise: logs an explicit degradation warning to stderr and returns the warning message
      for inclusion in `timeline.sync_warning`.
    """
    ver = get_ffmpeg_version()
    if not involves_m4a or ver.meets_m4a_requirement:
        return None

    env_strict = os.environ.get("AURORA_STRICT_FFMPEG", "").strip().lower() in ("1", "true", "yes")
    msg = (
        f"FFmpeg version {ver.major}.{ver.minor}.{ver.patch} ('{ver.raw_line}') is older than the minimum "
        f"recommended FFmpeg >= {MINIMUM_M4A_FFMPEG_MAJOR}.0 for sample-accurate M4A/AAC edit-list trailing "
        "packet trimming. Decoded M4A sample counts may include un-trimmed trailing AAC frame padding; "
        "use WAV 48 kHz / 24-bit or upgrade to FFmpeg >= 7."
    )
    if strict or env_strict:
        raise ProcessingError(
            msg,
            error_code="FFMPEG_VERSION_TOO_OLD",
            details={
                "detected_major": ver.major,
                "required_major": MINIMUM_M4A_FFMPEG_MAJOR,
                "ffmpeg_version": ver.raw_line,
            },
        )

    log_stderr(f"WARNING [FFMPEG_VERSION_DEGRADED]: {msg}")
    return msg


def compute_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file in 64 KB chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def probe_stream_metadata(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run `ffprobe` on `path` and return `(stream_dict, format_dict)` after validating audio stream."""
    _, ffprobe_bin = ensure_ffmpeg_available()
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
        (
            "stream=codec_name,sample_rate,channels,channel_layout,"
            "bits_per_raw_sample,bits_per_sample,initial_padding,trailing_padding,duration_ts"
        ),
        "-show_entries",
        "format=format_name,duration",
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
        fmt_info = probe_data.get("format", {})
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
    in_sr = int(stream.get("sample_rate", 0) or 0)
    in_ch = int(stream.get("channels", 0) or 0)
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

    return stream, fmt_info


def _build_demux_flags(path: Path, codec_name: str, *, ignore_gapless: bool) -> list[str]:
    demux_flags: list[str] = []
    if ignore_gapless:
        suffix = path.suffix.lower()
        if suffix in (".m4a", ".mp4", ".mov") or codec_name == "aac":
            if suffix != ".aac":
                demux_flags.extend(["-ignore_editlist", "1"])
        elif suffix == ".mp3" or codec_name == "mp3":
            demux_flags.extend(["-flags2", "+skip_manual"])
    return demux_flags


def stream_decode_f32le(
    path: Path,
    *,
    target_sample_rate: int | None = None,
    ignore_gapless: bool = False,
    block_frames: int = 1024,
) -> Generator[tuple[NDArray[np.float32], int, int], None, None]:
    """Stream-decode an audio file to `(frames, channels)` float32 numpy blocks via FFmpeg pipe.

    Peak memory is O(block_frames) regardless of file duration. Cleanly terminates the child
    FFmpeg process if interrupted or closed early.
    """
    ffmpeg_bin, _ = ensure_ffmpeg_available()
    stream, _ = probe_stream_metadata(path)
    in_sr = int(stream["sample_rate"])
    in_ch = int(stream["channels"])
    out_sr = target_sample_rate if target_sample_rate is not None else in_sr
    codec_name = str(stream.get("codec_name", "")).lower()

    demux_flags = _build_demux_flags(path, codec_name, ignore_gapless=ignore_gapless)
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

    if out_sr != in_sr:
        require_soxr(ffmpeg_bin)
        decode_cmd[decode_cmd.index("-ar"):decode_cmd.index("-ar")] = [
            "-af", f"aresample={out_sr}:resampler=soxr:precision=28",
        ]

    frame_bytes = 4 * in_ch
    chunk_bytes_target = max(1, block_frames) * frame_bytes

    proc = subprocess.Popen(
        decode_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    total_frames_yielded = 0
    leftover = b""
    completed_reading = False
    try:
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(chunk_bytes_target)
            if not raw:
                completed_reading = True
                break
            buf = leftover + raw
            valid_len = (len(buf) // frame_bytes) * frame_bytes
            leftover = buf[valid_len:]
            if valid_len == 0:
                continue
            arr = np.frombuffer(buf[:valid_len], dtype=np.float32).reshape(-1, in_ch).copy()
            if not np.all(np.isfinite(arr)):
                raise ProcessingError(
                    f"Decoded PCM from '{path.name}' contains non-finite (NaN/Inf) samples.",
                    error_code="NON_FINITE_SAMPLES",
                )
            total_frames_yielded += arr.shape[0]
            yield arr, out_sr, in_ch

        stderr_bytes = proc.stderr.read() if proc.stderr is not None else b""
        ret = proc.wait()
        if ret != 0:
            err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise InputAudioError(
                f"Failed to decode audio '{path.name}': {err_msg}",
                error_code="DECODE_FAILED",
            )
        if len(leftover) != 0 or total_frames_yielded == 0:
            raise InputAudioError(
                f"Decoded PCM stream from '{path.name}' is empty or misaligned.",
                error_code="EMPTY_OR_CORRUPT_AUDIO",
            )
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()
        if not completed_reading and proc.poll() is None:
            proc.kill()
            proc.wait()


def count_decoded_samples_streaming(
    path: Path,
    *,
    target_sample_rate: int | None = None,
    ignore_gapless: bool = False,
) -> tuple[int, int, int]:
    """Stream-decode `path` in 16384-frame chunks and return `(total_samples, sample_rate, channels)`."""
    total_samples = 0
    sr = 0
    ch = 0
    for block, block_sr, block_ch in stream_decode_f32le(
        path,
        target_sample_rate=target_sample_rate,
        ignore_gapless=ignore_gapless,
        block_frames=16384,
    ):
        total_samples += block.shape[0]
        sr = block_sr
        ch = block_ch
    return total_samples, sr, ch


def decode_to_f32le(
    path: Path,
    *,
    target_sample_rate: int | None = None,
    ignore_gapless: bool = False,
) -> tuple[bytes, int, int]:
    """Decode an audio file to interleaved 32-bit float little-endian PCM (`f32le`) bytes."""
    chunks: list[NDArray[np.float32]] = []
    sr = 0
    ch = 0
    for block, block_sr, block_ch in stream_decode_f32le(
        path,
        target_sample_rate=target_sample_rate,
        ignore_gapless=ignore_gapless,
        block_frames=16384,
    ):
        chunks.append(block)
        sr = block_sr
        ch = block_ch
    if not chunks:
        raise InputAudioError(
            f"Decoded PCM buffer from '{path.name}' is empty.",
            error_code="EMPTY_OR_CORRUPT_AUDIO",
        )
    full = np.ascontiguousarray(np.concatenate(chunks, axis=0), dtype=np.float32)
    return full.tobytes(), sr, ch


def probe_audio(path: Path, *, predecoded: tuple[bytes, int, int] | None = None) -> AudioProbeInfo:
    """Inspect an audio file and return complete `AudioProbeInfo` with exact decoded sample count."""
    stream, fmt_info = probe_stream_metadata(path)

    if predecoded is not None:
        pcm_bytes, sr, ch = predecoded
        samples = len(pcm_bytes) // (4 * ch)
    else:
        samples, sr, ch = count_decoded_samples_streaming(path)

    duration = samples / sr if sr > 0 else 0.0
    codec = str(stream.get("codec_name", "unknown"))
    container = str(fmt_info.get("format_name", "unknown"))

    if codec.startswith("pcm_"):
        fmt_label = "aiff" if "aiff" in container else "wav"
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


def _parse_ebur128_stderr(stderr_text: str) -> LoudnessMetrics:
    """Parse ITU-R BS.1770-4 summary output from `ffmpeg -af ebur128=peak=true`."""
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


def measure_loudness_file(path: Path) -> LoudnessMetrics:
    """Measure ITU-R BS.1770-4 integrated loudness, LRA, and True Peak directly from a file on disk."""
    ffmpeg_bin, _ = ensure_ffmpeg_available()
    cmd = [
        ffmpeg_bin,
        "-nostdin",
        "-nostats",
        "-hide_banner",
        "-i",
        str(path),
        "-vn",
        "-sn",
        "-dn",
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, check=False)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace").strip()
        raise ProcessingError(f"BS.1770 loudness measurement failed: {err}", error_code="LOUDNESS_FAILED")
    return _parse_ebur128_stderr(res.stderr.decode("utf-8", errors="replace"))


def measure_loudness_f32le(pcm_f32le: bytes, sample_rate: int, channels: int) -> LoudnessMetrics:
    """Measure ITU-R BS.1770-4 integrated loudness, LRA, and True Peak from in-memory f32le bytes."""
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
    return _parse_ebur128_stderr(res.stderr.decode("utf-8", errors="replace"))


def _build_encode_cmd(
    ffmpeg_bin: str,
    *,
    sample_rate: int,
    channels: int,
    output_spec: OutputSpec,
    target_path: Path,
    gain_db: float = 0.0,
    output_sample_rate: int | None = None,
    expected_samples: int | None = None,
    metadata_source: Path | None = None,
) -> list[str]:
    target_sr = output_sample_rate or output_spec.resolve_sample_rate(sample_rate)
    codec_args: list[str] = ["-c:a", output_spec.codec_name]
    fmt = output_spec.format
    if fmt == OutputFormat.WAV:
        codec_args.extend(["-rf64", "auto", "-f", "wav"])
    elif fmt in (OutputFormat.M4A, OutputFormat.ALAC):
        codec_args.extend(["-movflags", "+faststart", "-f", "ipod"])
        if fmt == OutputFormat.M4A:
            codec_args.extend(["-b:a", f"{output_spec.bitrate_kbps or 256}k"])
    elif fmt == OutputFormat.MP3:
        codec_args.extend(["-b:a", f"{output_spec.bitrate_kbps or 256}k", "-write_xing", "1", "-f", "mp3"])
    elif fmt == OutputFormat.FLAC:
        codec_args.extend(["-f", "flac"])
    elif fmt == OutputFormat.AIFF:
        codec_args.extend(["-write_id3v2", "1", "-f", "aiff"])
    if fmt in (OutputFormat.FLAC, OutputFormat.ALAC):
        sample_fmt = "s16" if output_spec.bit_depth == 16 else "s32"
        codec_args.extend(["-sample_fmt", sample_fmt + ("p" if fmt == OutputFormat.ALAC else "")])
        if output_spec.bit_depth == 24:
            codec_args.extend(["-bits_per_raw_sample", "24"])

    filters: list[str] = []
    if abs(gain_db) > 1e-6:
        filters.append(f"volume={gain_db:.6f}dB")
    if target_sr != sample_rate:
        require_soxr(ffmpeg_bin)
        filters.append(f"aresample={target_sr}:resampler=soxr:precision=28")
    # Quantize after resampling. TPDF must target s16 explicitly; otherwise FFmpeg
    # may insert a separate undithered float-to-int conversion after this filter.
    if output_spec.is_lossless and output_spec.bit_depth == 16:
        filters.append("aresample=osf=s16p:dither_method=triangular")
    if expected_samples is not None:
        filters.extend([f"apad=whole_len={expected_samples}", f"atrim=end_sample={expected_samples}"])
    metadata_args: list[str] = []
    if metadata_source is not None:
        metadata_args = ["-i", str(metadata_source), "-map", "0:a:0", "-map_metadata", "1",
                         "-map_metadata:s:a:0", "1:s:a:0"]
        cover = _cover_stream(metadata_source)
        if cover is not None and fmt != OutputFormat.WAV:
            metadata_args.extend(["-map", f"1:{cover}", "-c:v", "copy", "-disposition:v:0", "attached_pic"])
    return [
        ffmpeg_bin, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "f32le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
        *metadata_args,
        *(["-af", ",".join(filters)] if filters else []),
        "-ar", str(target_sr), "-ac", str(channels), *codec_args, str(target_path),
    ]


class StreamingF32LeEncoder:
    """Streaming FFmpeg encoder that accepts float32 numpy blocks over stdin pipe."""

    def __init__(
        self,
        *,
        sample_rate: int,
        channels: int,
        output_spec: OutputSpec,
        target_path: Path,
        gain_db: float = 0.0,
        output_sample_rate: int | None = None,
        expected_samples: int | None = None,
        metadata_source: Path | None = None,
    ) -> None:
        ffmpeg_bin, _ = ensure_ffmpeg_available()
        self.output_spec = output_spec
        self.target_path = target_path
        self.metadata_source = metadata_source
        self._cmd = _build_encode_cmd(
            ffmpeg_bin,
            sample_rate=sample_rate,
            channels=channels,
            output_spec=output_spec,
            target_path=target_path,
            gain_db=gain_db,
            output_sample_rate=output_sample_rate,
            expected_samples=expected_samples,
            metadata_source=metadata_source,
        )
        self._proc: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> StreamingF32LeEncoder:
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return self

    def write_block(self, block: NDArray[np.float32]) -> None:
        """Write a `(frames, channels)` float32 numpy block to the encoder pipe."""
        if self._proc is None or self._proc.stdin is None:
            raise ProcessingError("StreamingF32LeEncoder is not open.", error_code="ENCODE_FAILED")
        if block.shape[0] == 0:
            return
        contiguous = np.ascontiguousarray(block, dtype=np.float32)
        try:
            self._proc.stdin.write(contiguous.tobytes())
        except BrokenPipeError as exc:
            stderr_msg = ""
            if self._proc.stderr is not None:
                stderr_msg = self._proc.stderr.read().decode("utf-8", errors="replace").strip()
            raise ProcessingError(
                f"FFmpeg encoder pipe closed unexpectedly ({self.output_spec.format.value}): {stderr_msg}",
                error_code="ENCODE_FAILED",
            ) from exc

    def close(self) -> None:
        """Flush stdin, wait for FFmpeg completion, and verify exit code 0."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            stderr_bytes = proc.stderr.read() if proc.stderr is not None else b""
            ret = proc.wait()
            if ret != 0:
                err = stderr_bytes.decode("utf-8", errors="replace").strip()
                raise ProcessingError(
                    f"FFmpeg encoding to {self.output_spec.format.value} failed: {err}",
                    error_code="ENCODE_FAILED",
                )
        finally:
            if proc.stderr is not None:
                proc.stderr.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait()

        if self.output_spec.format == OutputFormat.WAV and self.metadata_source is not None:
            preserve_wav_cover(self.metadata_source, self.target_path)

    def abort(self) -> None:
        """Immediately terminate the encoder subprocess on cancellation or error."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        try:
            if proc.stderr is not None:
                proc.stderr.close()
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.abort()
        else:
            self.close()


def encode_f32le_to_path(
    pcm_f32le: bytes,
    *,
    sample_rate: int,
    channels: int,
    output_spec: OutputSpec,
    target_path: Path,
) -> None:
    """Encode raw interleaved float32le PCM bytes to `target_path`."""
    arr = np.frombuffer(pcm_f32le, dtype=np.float32).reshape(-1, channels)
    with StreamingF32LeEncoder(
        sample_rate=sample_rate,
        channels=channels,
        output_spec=output_spec,
        target_path=target_path,
    ) as enc:
        enc.write_block(arr)


def atomic_write_bytes(target_path: Path, data: bytes, *, overwrite: bool = False) -> None:
    """Atomically write bytes to `target_path`, respecting `overwrite` protection and cleaning up on interrupt."""
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
