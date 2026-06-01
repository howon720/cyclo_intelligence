# Copyright 2026 ROBOTIS CO., LTD.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Convert a recorded MP4 + frame index list into a synced MP4.

LeRobot's dataset format assumes a 1:1 mapping between parquet rows and
MP4 frames per episode. The recorder writes one frame per camera publish
event (variable rate), so the convert step has to re-pack the MP4 with
exactly one frame per resampled grid timestamp.

Strategy: stream-decode the input MP4 once, write selected frames directly
to a H.264 encoder, and fall back to the older JPEG-temp implementation if
the streaming path cannot handle the input. This avoids materialising every
source frame on disk in the common recording-v2 path.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Optional, Sequence

import numpy as np


_VIDEO_SYNC_TMPDIR_ENV = "CYCLO_VIDEO_SYNC_TMPDIR"
_VIDEO_SYNC_MIN_FREE_MB_ENV = "CYCLO_VIDEO_SYNC_MIN_FREE_MB"
_VIDEO_SYNC_FORCE_FALLBACK_ENV = "CYCLO_VIDEO_SYNC_FORCE_FALLBACK"
_VIDEO_SYNC_DISABLE_COPY_FASTPATH_ENV = "CYCLO_VIDEO_SYNC_DISABLE_COPY_FASTPATH"
_VIDEO_SYNC_ENABLE_FFMPEG_PIPE_ENV = "CYCLO_VIDEO_SYNC_ENABLE_FFMPEG_PIPE"
_VIDEO_SYNC_ENABLE_OPENCV_WRITER_ENV = "CYCLO_VIDEO_SYNC_ENABLE_OPENCV_WRITER"
_VIDEO_SYNC_DISABLE_OPENCV_WRITER_ENV = "CYCLO_VIDEO_SYNC_DISABLE_OPENCV_WRITER"
_VIDEO_SYNC_DISABLE_YUV420_PIPE_ENV = "CYCLO_VIDEO_SYNC_DISABLE_YUV420_PIPE"
_VIDEO_SYNC_STRICT_FFMPEG_DECODE_ENV = "CYCLO_VIDEO_SYNC_STRICT_FFMPEG_DECODE"
_VIDEO_STATS_SAMPLES_ENV = "CYCLO_VIDEO_STATS_SAMPLES"
_H264_ENCODER_ENV = "CYCLO_H264_ENCODER"
_H264_DECODER_ENV = "CYCLO_H264_DECODER"
_X264_SPEED_PROFILE_ENV = "CYCLO_X264_SPEED_PROFILE"
_X264_PRESET_ENV = "CYCLO_X264_PRESET"
_X264_CRF_ENV = "CYCLO_X264_CRF"
_X264_QP_ENV = "CYCLO_X264_QP"
_X264_TUNE_ENV = "CYCLO_X264_TUNE"
_X264_GOP_ENV = "CYCLO_X264_GOP"
_X264_THREADS_ENV = "CYCLO_X264_THREADS"
_MP4_FASTSTART_ENV = "CYCLO_MP4_FASTSTART"
_FFMPEG_PIPE_SIZE_ENV = "CYCLO_FFMPEG_PIPE_SIZE"
_DEFAULT_VIDEO_STATS_SAMPLES = 8


@dataclass
class VideoSyncResult:
    """Summary returned by :func:`remux_selected_frames`."""

    frame_count: int
    stats: Optional[dict[str, Any]] = None
    used_fallback: bool = False
    mode: str = "stream_encode"
    output_height: Optional[int] = None
    output_width: Optional[int] = None


def _ffmpeg() -> str:
    bin_ = shutil.which("ffmpeg")
    if bin_ is None:
        raise RuntimeError("ffmpeg not found on PATH")
    return bin_


_H264_ENCODER_CACHE: "dict[tuple[str, ...], list[str]] | None" = {}
_H264_ENCODER_CACHE_LOCK = threading.Lock()
_H264_ENCODER_THREAD_LOCAL = threading.local()
_GSTREAMER_ENCODER_PREFIX = "gstreamer:"
_GSTREAMER_H264_ENCODER = f"{_GSTREAMER_ENCODER_PREFIX}nvv4l2h264enc"
_GSTREAMER_H264_ENCODER_OPTS = ["bitrate=5000000"]
_GSTREAMER_WARNED_REASONS: set[str] = set()
_GSTREAMER_LD_LIBRARY_PATHS = ("/usr/lib/aarch64-linux-gnu/nvidia",)
_NVENC_MIN_DIMENSION = 256
_NVENC_ENCODER_OPTS = [
    "-preset", "p1",
    "-tune", "ll",
    "-rc", "vbr",
    "-cq", "23",
    "-bf", "0",
    "-zerolatency", "1",
]
_NVENC_MAX_ENCODER_OPTS = [
    "-preset", "p1",
    "-tune", "ll",
    "-rc", "constqp",
    "-qp", "51",
    "-bf", "0",
    "-zerolatency", "1",
]
_HW_BITRATE_ENCODER_OPTS = ["-b:v", "5M"]
_UINT8_VALUES = np.arange(256, dtype=np.float64)
_UINT8_SQUARES = _UINT8_VALUES * _UINT8_VALUES


def _is_max_speed_profile() -> bool:
    profile = os.environ.get(_X264_SPEED_PROFILE_ENV, "").strip().lower()
    return profile in {"max", "maximum", "max_speed", "fastest"}


def _x264_profile_defaults() -> tuple[str, str, str, str, str]:
    """Return preset/rate-mode/rate-value/tune/GOP defaults for x264."""
    profile = os.environ.get(_X264_SPEED_PROFILE_ENV, "fast").strip().lower()
    if _is_max_speed_profile():
        return "ultrafast", "qp", "51", "zerolatency", "1"
    if profile in {"quality", "balanced", "legacy", "safe"}:
        return "ultrafast", "crf", "23", "", ""
    return "ultrafast", "crf", "32", "zerolatency", ""


def _libx264_encoder() -> tuple[str, list[str]]:
    default_preset, default_rate_mode, default_rate, default_tune, default_gop = (
        _x264_profile_defaults()
    )
    preset = os.environ.get(_X264_PRESET_ENV, default_preset).strip() or default_preset
    qp = os.environ.get(_X264_QP_ENV, "").strip()
    crf = os.environ.get(_X264_CRF_ENV, "").strip()
    opts = ["-preset", preset]
    if qp:
        opts.extend(["-qp", qp])
    elif crf:
        opts.extend(["-crf", crf])
    else:
        opts.extend([f"-{default_rate_mode}", default_rate])
    if _X264_TUNE_ENV in os.environ:
        tune = os.environ.get(_X264_TUNE_ENV, "").strip()
    else:
        tune = default_tune
    if tune and tune.lower() not in {"0", "false", "none", "off"}:
        opts.extend(["-tune", tune])
    gop = os.environ.get(_X264_GOP_ENV, default_gop).strip()
    if gop:
        opts.extend(["-g", gop])
    threads = os.environ.get(
        _X264_THREADS_ENV,
        "1" if _is_max_speed_profile() else "",
    ).strip()
    if threads:
        try:
            if int(threads) > 0:
                opts.extend(["-threads", threads])
        except ValueError:
            pass
    return "libx264", opts


@contextmanager
def _force_h264_software_encoder():
    """Force libx264 only in the current thread."""
    sentinel = object()
    previous = getattr(_H264_ENCODER_THREAD_LOCAL, "force_software", sentinel)
    _H264_ENCODER_THREAD_LOCAL.force_software = True
    try:
        yield
    finally:
        if previous is sentinel:
            try:
                delattr(_H264_ENCODER_THREAD_LOCAL, "force_software")
            except AttributeError:
                pass
        else:
            _H264_ENCODER_THREAD_LOCAL.force_software = previous


def _nvenc_encoder_opts() -> list[str]:
    if _is_max_speed_profile():
        return list(_NVENC_MAX_ENCODER_OPTS)
    return list(_NVENC_ENCODER_OPTS)


@lru_cache(maxsize=1)
def _running_on_jetson() -> bool:
    """Best-effort detection for Jetson/Orin edge devices."""
    marker_paths = (
        Path("/etc/nv_tegra_release"),
        Path("/sys/module/tegra_fuse"),
    )
    if any(path.exists() for path in marker_paths):
        return True
    try:
        model = Path("/proc/device-tree/model").read_text(
            encoding="utf-8",
            errors="ignore",
        ).lower()
    except OSError:
        return False
    return any(token in model for token in ("jetson", "orin", "tegra"))


def _jetson_hardware_encoder_candidates() -> list[tuple[str, list[str]]]:
    """Return Jetson/Orin hardware encoders in preferred probe order."""
    return [
        ("h264_nvmpi", list(_HW_BITRATE_ENCODER_OPTS)),
        ("h264_v4l2m2m", list(_HW_BITRATE_ENCODER_OPTS)),
        ("h264_omx", list(_HW_BITRATE_ENCODER_OPTS)),
        ("h264_nvenc", _nvenc_encoder_opts()),
    ]


def _gstreamer_hardware_encoder_candidates() -> list[tuple[str, list[str]]]:
    return [(_GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS))]


def _h264_hardware_encoder_candidates() -> list[tuple[str, list[str]]]:
    """Return opt-in hardware encoders in platform-sensitive probe order."""
    if _running_on_jetson():
        return _jetson_hardware_encoder_candidates()
    return [
        ("h264_nvenc", _nvenc_encoder_opts()),
        ("h264_nvmpi", list(_HW_BITRATE_ENCODER_OPTS)),
        ("h264_v4l2m2m", list(_HW_BITRATE_ENCODER_OPTS)),
        ("h264_omx", list(_HW_BITRATE_ENCODER_OPTS)),
    ]


def _ffmpeg_threads_arg() -> list[str]:
    """Return ``["-threads", "N"]`` for ffmpeg codec thread placement.

    FFmpeg option placement matters: before ``-i`` this caps decoder-side
    work, while after ``-c:v`` it caps the encoder. Direct aggregation runs
    several decoders concurrently, so the default stays conservative: two
    threads on normal multi-core systems, one on very small systems. Override with
    ``CYCLO_FFMPEG_THREADS`` for hosts whose benchmark favors a different
    decoder/thread balance.
    """
    default_threads = "2" if (os.cpu_count() or 1) >= 4 else "1"
    raw = os.environ.get("CYCLO_FFMPEG_THREADS", default_threads)
    try:
        n = max(1, int(raw))
    except ValueError:
        n = 1
    return ["-threads", str(n)]


def _is_gstreamer_encoder(encoder: str) -> bool:
    return str(encoder).startswith(_GSTREAMER_ENCODER_PREFIX)


def _warn_gstreamer_probe(reason: str) -> None:
    reason = str(reason).strip() or "unknown error"
    if reason in _GSTREAMER_WARNED_REASONS:
        return
    _GSTREAMER_WARNED_REASONS.add(reason)
    import sys as _sys
    print(
        f"video_sync: Jetson GStreamer H.264 probe unavailable: {reason}",
        file=_sys.stderr,
    )


def _gstreamer_bin(name: str) -> str | None:
    return shutil.which(name)


def _gstreamer_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("LD_LIBRARY_PATH", "")
    paths = [p for p in _GSTREAMER_LD_LIBRARY_PATHS if Path(p).exists()]
    if paths:
        parts = paths + ([existing] if existing else [])
        env["LD_LIBRARY_PATH"] = ":".join(parts)
    return env


def _gst_inspect_has(plugin: str) -> bool:
    inspect = _gstreamer_bin("gst-inspect-1.0")
    if inspect is None:
        return False
    try:
        res = subprocess.run(
            [inspect, plugin],
            capture_output=True,
            text=True,
            timeout=10,
            env=_gstreamer_env(),
        )
        return res.returncode == 0
    except Exception:
        return False


def _gstreamer_h264_command(
    *,
    input_pix_fmt: str,
    width: int,
    height: int,
    fps: int,
    output_mp4: Path,
    opts: Sequence[str],
) -> list[str]:
    gst = _gstreamer_bin("gst-launch-1.0")
    if gst is None:
        raise RuntimeError("gst-launch-1.0 not found")

    pix_fmt = str(input_pix_fmt).lower()
    if pix_fmt == "yuv420p":
        gst_format = "i420"
        convert = ["nvvidconv"]
    elif pix_fmt == "bgr24":
        gst_format = "bgr"
        convert = ["videoconvert", "!", "nvvidconv"]
    else:
        raise RuntimeError(f"unsupported GStreamer raw format: {input_pix_fmt}")

    encoder_props = list(opts) if opts else list(_GSTREAMER_H264_ENCODER_OPTS)
    return [
        gst, "-q", "-e",
        "fdsrc", "fd=0",
        "!", "rawvideoparse",
        f"format={gst_format}",
        f"width={int(width)}",
        f"height={int(height)}",
        f"framerate={int(fps)}/1",
        "!", "queue",
        "!", *convert,
        "!", "video/x-raw(memory:NVMM),format=NV12",
        "!", "nvv4l2h264enc",
        *encoder_props,
        "!", "h264parse",
        "!", "qtmux",
        "!", "filesink", f"location={str(output_mp4)}",
    ]


@lru_cache(maxsize=8)
def _try_gstreamer_h264_encoder(width: int, height: int, fps: int) -> bool:
    if _gstreamer_bin("gst-launch-1.0") is None:
        _warn_gstreamer_probe("gst-launch-1.0 not found")
        return False
    required_plugins = (
        "rawvideoparse",
        "nvvidconv",
        "nvv4l2h264enc",
        "h264parse",
        "qtmux",
    )
    missing = [plugin for plugin in required_plugins if not _gst_inspect_has(plugin)]
    if missing:
        _warn_gstreamer_probe(f"missing plugin(s): {', '.join(missing)}")
        return False

    width = max(256, int(width or 256))
    height = max(256, int(height or 256))
    if width % 2:
        width += 1
    if height % 2:
        height += 1
    frame = bytes(width * height * 3 // 2)
    with tempfile.TemporaryDirectory(prefix="cyclo_gst_probe_") as tmpdir:
        out = Path(tmpdir) / "probe.mp4"
        try:
            cmd = _gstreamer_h264_command(
                input_pix_fmt="yuv420p",
                width=width,
                height=height,
                fps=int(fps or 15),
                output_mp4=out,
                opts=_GSTREAMER_H264_ENCODER_OPTS,
            )
            res = subprocess.run(
                cmd,
                input=frame,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
                env=_gstreamer_env(),
            )
            if res.returncode == 0 and out.exists() and out.stat().st_size > 0:
                return True
            stderr = res.stderr.decode(errors="replace")[-300:]
            _warn_gstreamer_probe(stderr or f"gst-launch rc={res.returncode}")
        except Exception as exc:
            _warn_gstreamer_probe(repr(exc))
    return False


@lru_cache(maxsize=4)
def _ffmpeg_supports_h264_cuvid(ffmpeg: str) -> bool:
    """Return True when this ffmpeg can actually decode H.264 via CUVID."""
    return _ffmpeg_supports_h264_decoder(ffmpeg, "h264_cuvid")


@lru_cache(maxsize=16)
def _ffmpeg_supports_h264_decoder(ffmpeg: str, decoder: str) -> bool:
    """Return True when ffmpeg can actually decode H.264 with ``decoder``."""
    try:
        listed = subprocess.run(
            [ffmpeg, "-hide_banner", "-decoders"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if listed.returncode != 0 or decoder not in listed.stdout:
            return False
        with tempfile.TemporaryDirectory(prefix="cyclo_h264dec_probe_") as tmpdir:
            sample = Path(tmpdir) / "sample.mp4"
            make_sample = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi",
                    "-i", "color=c=black:s=256x256:r=1:d=0.1",
                    "-frames:v", "1",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    str(sample),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if make_sample.returncode != 0:
                return False
            decode = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-c:v", decoder,
                    "-i", str(sample),
                    "-frames:v", "1",
                    "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            return decode.returncode == 0
    except Exception:
        return False


def _first_usable_h264_decoder(ffmpeg: str, candidates: Sequence[str]) -> Optional[str]:
    for decoder in candidates:
        if decoder == "h264_cuvid":
            if _ffmpeg_supports_h264_cuvid(ffmpeg):
                return decoder
        elif _ffmpeg_supports_h264_decoder(ffmpeg, decoder):
            return decoder
    return None


def _ffmpeg_h264_decoder_args(ffmpeg: str) -> list[str]:
    """Return ffmpeg input args for the fastest available H.264 decoder.

    Multi-job direct aggregation is generally faster with the portable software
    decoder on ordinary Linux hosts. Auto-probe Jetson/Orin hardware decoders
    and expose explicit
    ``CYCLO_H264_DECODER=h264_cuvid``/``h264_nvmpi``/``h264_v4l2m2m``/``auto``
    for hosts where hardware decode wins.
    """
    raw = os.environ.get(_H264_DECODER_ENV)
    requested = (
        "jetson" if raw is None and _running_on_jetson()
        else (raw or "").strip().lower()
    )
    if not requested:
        return _ffmpeg_threads_arg()
    if requested in {"0", "false", "no", "off", "none", "native", "software"}:
        return _ffmpeg_threads_arg()
    aliases = {
        "cuda": ["h264_cuvid"],
        "cuvid": ["h264_cuvid"],
        "h264_cuvid": ["h264_cuvid"],
        "nvdec": ["h264_cuvid"],
        "nvmpi": ["h264_nvmpi"],
        "h264_nvmpi": ["h264_nvmpi"],
        "jetson": ["h264_nvmpi", "h264_v4l2m2m", "h264_cuvid"],
        "v4l2m2m": ["h264_v4l2m2m"],
        "h264_v4l2m2m": ["h264_v4l2m2m"],
        "auto": (
            ["h264_nvmpi", "h264_v4l2m2m", "h264_cuvid"]
            if _running_on_jetson()
            else ["h264_cuvid", "h264_nvmpi", "h264_v4l2m2m"]
        ),
    }
    candidates = aliases.get(requested)
    if candidates is not None:
        decoder = _first_usable_h264_decoder(ffmpeg, candidates)
        if decoder:
            return ["-c:v", decoder]
        return _ffmpeg_threads_arg()
    # Unknown values behave like auto so host-specific tuning typos do not
    # fail a conversion that has a perfectly good software fallback.
    decoder = _first_usable_h264_decoder(
        ffmpeg,
        ["h264_cuvid", "h264_nvmpi", "h264_v4l2m2m"],
    )
    if decoder:
        return ["-c:v", decoder]
    return _ffmpeg_threads_arg()


def _ffmpeg_pipe_size(frame_size: int) -> int:
    """Return desired OS pipe capacity for raw frame streaming."""
    raw = os.environ.get(_FFMPEG_PIPE_SIZE_ENV)
    if raw is not None:
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    # Kernel defaults are the safest portable baseline. Leave larger pipes as
    # an explicit host-tuning knob because the best value varies by kernel and
    # concurrent ffmpeg workload.
    return 0


def _set_pipe_size(pipe, size: int) -> None:
    if size <= 0 or pipe is None:
        return
    try:
        import fcntl

        fcntl.fcntl(pipe.fileno(), fcntl.F_SETPIPE_SZ, int(size))
    except Exception:
        pass


def _mp4_faststart_args() -> list[str]:
    if _MP4_FASTSTART_ENV not in os.environ:
        if _is_max_speed_profile():
            return []
    raw = os.environ.get(_MP4_FASTSTART_ENV, "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return []
    return ["-movflags", "+faststart"]


def _resolve_tmp_parent(output_mp4: Path) -> tuple[Path, bool]:
    override = os.environ.get(_VIDEO_SYNC_TMPDIR_ENV)
    if override:
        parent = Path(override)
        cleanup_when_empty = False
    else:
        parent = output_mp4.parent / ".video_sync_tmp"
        cleanup_when_empty = True
    parent.mkdir(parents=True, exist_ok=True)
    return parent, cleanup_when_empty


def _resolve_min_free_mb() -> int:
    raw = os.environ.get(_VIDEO_SYNC_MIN_FREE_MB_ENV, "0")
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _check_tmp_free_space(tmp_parent: Path) -> None:
    min_free_mb = _resolve_min_free_mb()
    if min_free_mb <= 0:
        return
    free_mb = shutil.disk_usage(tmp_parent).free / (1024 * 1024)
    if free_mb < min_free_mb:
        raise RuntimeError(
            f"video_sync temp dir {tmp_parent} has {free_mb:.1f} MB free; "
            f"requires at least {min_free_mb} MB "
            f"({_VIDEO_SYNC_MIN_FREE_MB_ENV})"
        )


def _cleanup_tmp_parent(tmp_parent: Path, cleanup_when_empty: bool) -> None:
    if not cleanup_when_empty:
        return
    try:
        tmp_parent.rmdir()
    except OSError:
        pass


def _try_encoder(ffmpeg: str, encoder: str, opts: list[str]) -> bool:
    """Smoke-test an encoder by running a 1-frame null-out encode.

    ffmpeg lists encoders that are *compiled in* regardless of whether
    the runtime can actually use them (e.g. h264_nvenc inside a
    container without nvidia-uvm / GPU passthrough). The only reliable
    check is to try a tiny encode and see if the process exits 0.
    """
    # Keep the probe representative of real camera frames. Some hardware
    # encoders reject tiny 64x64 inputs even though they work for normal camera
    # resolutions; probing too small falsely disables a usable hardware encoder
    # and falls back to libx264.
    if _is_gstreamer_encoder(encoder):
        return _try_gstreamer_h264_encoder(256, 256, 15)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=15:d=0.1",
        "-c:v", encoder, *opts,
        "-pix_fmt", "yuv420p",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=10)
        return res.returncode == 0
    except Exception:
        return False


def _h264_encoder(
    ffmpeg: str,
    *,
    width: int | None = None,
    height: int | None = None,
    allow_gstreamer: bool = False,
) -> tuple[str, list[str]]:
    """Pick an H.264 encoder + options.

    On ordinary Linux hosts the normal default is
    ``libx264 -preset ultrafast -crf 32 -tune zerolatency``: the portable
    baseline should not assume an NVIDIA runtime, and CRF32 cuts I/O while
    preserving the CFR H.264 contract. Jetson/Orin devices default to hardware
    auto-probe because their CPU budget is usually the bottleneck; failed
    probes fall back to libx264.

    Set ``CYCLO_X264_SPEED_PROFILE=quality`` for the old CRF23 default, or
    ``CYCLO_X264_SPEED_PROFILE=max`` to prioritize throughput. Max profile
    probes hardware encoders first when ``CYCLO_H264_ENCODER`` is unset, and
    falls back to a constant-QP all-intra x264 profile optimized for throughput.

    Set ``CYCLO_X264_PRESET`` / ``CYCLO_X264_CRF`` / ``CYCLO_X264_QP`` /
    ``CYCLO_X264_TUNE`` / ``CYCLO_X264_GOP`` / ``CYCLO_X264_THREADS`` to
    override the profile without changing conversion call sites.
    Set ``CYCLO_H264_ENCODER=auto`` to probe hardware encoders first,
    ``CYCLO_H264_ENCODER=gstreamer`` / ``jetson_gst`` / ``nvv4l2h264enc``
    for Jetson GStreamer where the caller supports it, or
    ``CYCLO_H264_ENCODER=h264_nvenc`` / ``h264_nvmpi`` / ``h264_v4l2m2m`` /
    ``libx264`` to force one explicitly for a host whose measured bottleneck
    differs.
    """
    if getattr(_H264_ENCODER_THREAD_LOCAL, "force_software", False):
        return _libx264_encoder()

    requested_raw = os.environ.get(_H264_ENCODER_ENV)
    if requested_raw is None:
        requested = (
            "auto"
            if _running_on_jetson() or _is_max_speed_profile()
            else "libx264"
        )
    else:
        requested = requested_raw.strip().lower()
    if requested in ("", "default", "libx264", "x264", "software"):
        return _libx264_encoder()

    if requested in ("gstreamer", "gst", "jetson_gst", "nvv4l2h264enc"):
        if allow_gstreamer and _try_gstreamer_h264_encoder(
            int(width or 256),
            int(height or 256),
            15,
        ):
            return _GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS)
        return _libx264_encoder()

    if requested == "jetson":
        if allow_gstreamer:
            for name, opts in _gstreamer_hardware_encoder_candidates():
                if _try_encoder(ffmpeg, name, opts):
                    return name, list(opts)
        for name, opts in _jetson_hardware_encoder_candidates():
            if name == "h264_nvenc" and (
                width is not None
                and height is not None
                and min(int(width), int(height)) < _NVENC_MIN_DIMENSION
            ):
                continue
            if _try_encoder(ffmpeg, name, opts):
                return name, list(opts)
        return _libx264_encoder()

    explicit: dict[str, tuple[str, list[str]]] = {
        "gstreamer": (_GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS)),
        "gst": (_GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS)),
        "jetson_gst": (_GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS)),
        "nvv4l2h264enc": (_GSTREAMER_H264_ENCODER, list(_GSTREAMER_H264_ENCODER_OPTS)),
        "nvenc": ("h264_nvenc", list(_NVENC_ENCODER_OPTS)),
        "nvmpi": ("h264_nvmpi", list(_HW_BITRATE_ENCODER_OPTS)),
        "v4l2m2m": ("h264_v4l2m2m", list(_HW_BITRATE_ENCODER_OPTS)),
        "omx": ("h264_omx", list(_HW_BITRATE_ENCODER_OPTS)),
    }
    explicit.update({
        name: (name, list(opts))
        for name, opts in _h264_hardware_encoder_candidates()
    })
    if requested in explicit:
        name, opts = explicit[requested]
        if _is_gstreamer_encoder(name) and not allow_gstreamer:
            name, opts = _libx264_encoder()
        elif name == "h264_nvenc" and (
            width is not None
            and height is not None
            and min(int(width), int(height)) < _NVENC_MIN_DIMENSION
        ):
            name, opts = _libx264_encoder()
        elif not _try_encoder(ffmpeg, name, opts):
            name, opts = _libx264_encoder()
        return name, list(opts)

    # Unknown values are treated like ``auto`` rather than failing the
    # conversion over a tuning typo.
    # NVENC can be available for normal camera frames while rejecting
    # tiny synthetic videos. Keep small clips on libx264 so unit tests
    # and thumbnail-sized assets stay correct without poisoning the
    # hardware-encoder cache used by real recordings.
    if (
        width is not None
        and height is not None
        and min(int(width), int(height)) < _NVENC_MIN_DIMENSION
    ):
        name, opts = _libx264_encoder()
        return name, list(opts)

    global _H264_ENCODER_CACHE
    cache_key = (
        requested,
        "max" if _is_max_speed_profile() else "normal",
        os.environ.get(_X264_PRESET_ENV, ""),
        os.environ.get(_X264_CRF_ENV, ""),
        os.environ.get(_X264_QP_ENV, ""),
        os.environ.get(_X264_TUNE_ENV, ""),
        os.environ.get(_X264_GOP_ENV, ""),
        os.environ.get(_X264_THREADS_ENV, ""),
        "gst" if allow_gstreamer else "ffmpeg",
    )
    with _H264_ENCODER_CACHE_LOCK:
        if (
            isinstance(_H264_ENCODER_CACHE, dict)
            and cache_key in _H264_ENCODER_CACHE
        ):
            name, *opts = _H264_ENCODER_CACHE[cache_key]
            return name, opts

        candidates = []
        if allow_gstreamer and _running_on_jetson():
            candidates.extend(_gstreamer_hardware_encoder_candidates())
        candidates.extend(_h264_hardware_encoder_candidates())
        candidates.append(_libx264_encoder())
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=False,
        )
        listed = probe.stdout
        for name, opts in candidates:
            if not _is_gstreamer_encoder(name) and name not in listed:
                continue
            if _try_encoder(ffmpeg, name, opts):
                if not isinstance(_H264_ENCODER_CACHE, dict):
                    _H264_ENCODER_CACHE = {}
                _H264_ENCODER_CACHE[cache_key] = [name, *opts]
                return name, opts
    # Should never fall through (libx264 is bundled with our ffmpeg) —
    # but raise rather than silently fall back to copy.
    raise RuntimeError("No usable H.264 encoder found on this ffmpeg")


def _rotation_transpose(rotation_deg: int) -> str | None:
    """Map ``rotation_deg`` (0/90/180/270) to ffmpeg ``-vf transpose`` value."""
    deg = int(rotation_deg or 0) % 360
    if deg == 0:
        return None
    if deg == 90:
        return "transpose=1"
    if deg == 180:
        return "transpose=2,transpose=2"
    if deg == 270:
        return "transpose=2"
    return None


def _video_filter_args(
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> list[str]:
    """Build ffmpeg ``-vf`` args for rotation then resize."""
    vf_filters: list[str] = []
    rot_filter = _rotation_transpose(rotation_deg)
    if rot_filter:
        vf_filters.append(rot_filter)
    if image_resize is not None:
        h, w = int(image_resize[0]), int(image_resize[1])
        if h > 0 and w > 0:
            vf_filters.append(f"scale={w}:{h}")
    return ["-vf", ",".join(vf_filters)] if vf_filters else []


def _output_dimensions(
    input_height: int,
    input_width: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> tuple[int, int]:
    """Return output height/width after converter rotation/resize filters."""
    height, width = int(input_height), int(input_width)
    if int(rotation_deg or 0) % 180:
        height, width = width, height
    if image_resize is not None:
        resized_h, resized_w = int(image_resize[0]), int(image_resize[1])
        if resized_h > 0 and resized_w > 0:
            height, width = resized_h, resized_w
    return height, width


def _video_frame_count(video_path: Path) -> Optional[int]:
    """Return decoded frame count using ffprobe, or None if unknown."""
    frame_count, _ = _video_frame_count_and_fps(video_path)
    return frame_count


def _parse_ffprobe_kv(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip()
    return values


def _parse_frame_count(
    values: dict[str, str], keys: tuple[str, ...],
) -> Optional[int]:
    for key in keys:
        raw = values.get(key)
        if raw and raw != "N/A":
            try:
                return int(raw)
            except ValueError:
                continue
    return None


def _parse_fps_rate(rate: Any) -> Optional[float]:
    if isinstance(rate, str) and "/" in rate:
        num, _, den = rate.partition("/")
        try:
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        except ValueError:
            return None
    try:
        return float(rate)
    except (TypeError, ValueError):
        return None


def _video_frame_count_and_fps(
    video_path: Path,
) -> tuple[Optional[int], Optional[float]]:
    """Return (frame_count, fps), sharing the common ffprobe invocation."""
    try:
        fast_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames,avg_frame_rate,r_frame_rate",
            "-of", "default=noprint_wrappers=1",
            str(video_path),
        ]
        fast = subprocess.run(
            fast_cmd, capture_output=True, text=True, timeout=30
        )
        fps: Optional[float] = None
        if fast.returncode == 0:
            values = _parse_ffprobe_kv(fast.stdout)
            frame_count = _parse_frame_count(values, ("nb_frames",))
            fps = _parse_fps_rate(
                values.get("avg_frame_rate") or values.get("r_frame_rate")
            )
            if frame_count is not None:
                return frame_count, fps

        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames,nb_frames,avg_frame_rate,r_frame_rate",
            "-of", "default=noprint_wrappers=1",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            return None, fps
        values = _parse_ffprobe_kv(res.stdout)
        frame_count = _parse_frame_count(
            values, ("nb_read_frames", "nb_frames")
        )
        fps = _parse_fps_rate(
            values.get("avg_frame_rate") or values.get("r_frame_rate")
        )
        return frame_count, fps
    except Exception:
        return None, None


def _probe_video_stream(video_path: Path) -> Optional[dict[str, Any]]:
    """Return ffprobe's first video stream dict, or None when unavailable."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_streams",
            "-print_format", "json",
            str(video_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            return None
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return None
        return streams[0]
    except Exception:
        return None


def _quick_video_dimensions(video_path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` using OpenCV metadata before ffprobe."""
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        try:
            if cap.isOpened():
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                if width > 0 and height > 0:
                    return width, height
        finally:
            cap.release()
    except Exception:
        pass

    stream = _probe_video_stream(video_path)
    if not stream:
        return 0, 0
    return int(stream.get("width") or 0), int(stream.get("height") or 0)


def _video_fps(video_path: Path) -> Optional[float]:
    stream = _probe_video_stream(video_path)
    if not stream:
        return None
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    return _parse_fps_rate(rate)


def _video_decodes_successfully(video_path: Path, ffmpeg: str) -> bool:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        return False
    if not os.environ.get(_VIDEO_SYNC_STRICT_FFMPEG_DECODE_ENV):
        try:
            import cv2

            cap = cv2.VideoCapture(str(video_path))
            try:
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        return True
            finally:
                cap.release()
        except Exception:
            pass
    try:
        result = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-v", "error", "-xerror",
                "-i", str(video_path),
                "-frames:v", "1",
                "-f", "null", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _video_count_fps_decode_probe(
    video_path: Path,
    ffmpeg: str,
    *,
    require_decode: bool = True,
) -> tuple[Optional[int], Optional[float], bool, bool]:
    """Return count/FPS/decode status, preferring one OpenCV file open."""
    if not video_path.exists() or video_path.stat().st_size <= 0:
        return None, None, False, False
    if not os.environ.get(_VIDEO_SYNC_STRICT_FFMPEG_DECODE_ENV):
        try:
            import cv2

            cap = cv2.VideoCapture(str(video_path))
            try:
                if cap.isOpened():
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    decode_ok = True
                    if require_decode:
                        decode_ok, _ = cap.read()
                    return (
                        frame_count if frame_count > 0 else None,
                        fps if fps > 0 else None,
                        bool(decode_ok),
                        True,
                    )
            finally:
                cap.release()
        except Exception:
            pass

    frame_count, fps = _video_frame_count_and_fps(video_path)
    decode_ok = (
        _video_decodes_successfully(video_path, ffmpeg)
        if require_decode else True
    )
    return frame_count, fps, decode_ok, False


def _validated_video_count(
    *,
    output_mp4: Path,
    expected_frames: int,
    target_fps: int,
    ffmpeg: str,
    label: str,
    require_decode: bool = True,
) -> int:
    produced_frames, fps, decode_ok, used_fast_probe = (
        _video_count_fps_decode_probe(output_mp4, ffmpeg, require_decode=require_decode)
    )
    fps_mismatch = fps is None or abs(fps - float(target_fps)) > 0.01
    frame_mismatch = produced_frames != expected_frames
    if used_fast_probe and (frame_mismatch or fps_mismatch or not decode_ok):
        produced_frames, fps = _video_frame_count_and_fps(output_mp4)
        decode_ok = (
            _video_decodes_successfully(output_mp4, ffmpeg)
            if require_decode else True
        )
        fps_mismatch = fps is None or abs(fps - float(target_fps)) > 0.01
        frame_mismatch = produced_frames != expected_frames

    if frame_mismatch:
        output_mp4.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} frame count mismatch for {output_mp4.name}: "
            f"expected {expected_frames}, got {produced_frames}"
        )
    if fps_mismatch:
        output_mp4.unlink(missing_ok=True)
        raise RuntimeError(
            f"{label} FPS mismatch for {output_mp4.name}: "
            f"expected {target_fps:g}, got {fps}"
        )
    if require_decode and not decode_ok:
        output_mp4.unlink(missing_ok=True)
        raise RuntimeError(f"{label} decode validation failed: {output_mp4.name}")
    return int(produced_frames)


@lru_cache(maxsize=8)
def _ffmpeg_supports_setts(ffmpeg: str) -> bool:
    try:
        res = subprocess.run(
            [ffmpeg, "-hide_banner", "-bsfs"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode != 0:
            return False
        return any(line.strip() == "setts" for line in res.stdout.splitlines())
    except Exception:
        return False


def _is_sequential_prefix(frame_indices: Sequence[int]) -> bool:
    return all(int(idx) == expected for expected, idx in enumerate(frame_indices))


def _can_packet_copy_sync(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> bool:
    """Return True when sync can rewrite packet timing without pixel encode."""
    if os.environ.get(_VIDEO_SYNC_DISABLE_COPY_FASTPATH_ENV):
        return False
    if rotation_deg or image_resize is not None:
        return False
    if target_fps <= 0 or 90000 % int(target_fps) != 0:
        return False
    if not _is_sequential_prefix(frame_indices):
        return False
    stream = _probe_video_stream(input_mp4)
    if not stream or stream.get("codec_name") != "h264":
        return False
    try:
        if int(stream.get("has_b_frames", 0) or 0) > 0:
            return False
    except (TypeError, ValueError):
        return False
    return _ffmpeg_supports_setts(ffmpeg)


def _remux_selected_frames_packet_copy(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    ffmpeg: str,
) -> VideoSyncResult:
    """Copy H.264 packets while rewriting timestamps to the target CFR grid."""
    expected_frames = len(frame_indices)
    ticks_per_frame = 90000 // int(target_fps)
    setts = (
        f"setts=pts=N*{ticks_per_frame}:"
        f"dts=N*{ticks_per_frame}:"
        f"duration={ticks_per_frame}:time_base=1/90000"
    )
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(input_mp4),
        "-map", "0:v:0",
        "-frames:v", str(expected_frames),
        "-c:v", "copy",
        "-bsf:v", setts,
        "-an",
        "-video_track_timescale", "90000",
        *_mp4_faststart_args(),
        str(output_mp4),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        output_mp4.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg packet-copy rc={result.returncode}: {result.stderr[-500:]}")

    produced_frames = _validated_video_count(
        output_mp4=output_mp4,
        expected_frames=expected_frames,
        target_fps=target_fps,
        ffmpeg=ffmpeg,
        label="packet-copy",
    )

    stream = _probe_video_stream(input_mp4)
    output_height = int(stream.get("height") or 0) if stream else None
    output_width = int(stream.get("width") or 0) if stream else None
    return VideoSyncResult(
        frame_count=int(produced_frames),
        stats=None,
        used_fallback=False,
        mode="packet_copy",
        output_height=output_height,
        output_width=output_width,
    )


class _StreamingRgbStats:
    """Accumulate RGB stats without retaining sampled frames in memory."""

    def __init__(self) -> None:
        self.frame_count = 0
        self.pixel_count = 0
        self.sum = np.zeros(3, dtype=np.float64)
        self.sumsq = np.zeros(3, dtype=np.float64)
        self.min = np.full(3, np.inf, dtype=np.float64)
        self.max = np.full(3, -np.inf, dtype=np.float64)

    def add_rgb(self, frame_rgb: np.ndarray) -> None:
        self._add_channels(frame_rgb, (0, 1, 2))

    def add_bgr(self, frame_bgr: np.ndarray) -> None:
        self._add_channels(frame_bgr, (2, 1, 0))

    def _add_channels(self, frame: np.ndarray, order: tuple[int, int, int]) -> None:
        if frame.size == 0:
            return
        self.frame_count += 1
        self.pixel_count += int(frame.shape[0] * frame.shape[1])
        if self._add_channels_cv(frame, order):
            return
        self._add_channels_hist(frame, order)

    def _add_channels_cv(
        self,
        frame: np.ndarray,
        order: tuple[int, int, int],
    ) -> bool:
        """Use OpenCV's C++ reductions for sampled-frame statistics."""
        try:
            import cv2

            mean, std = cv2.meanStdDev(frame)
            mean_arr = mean.reshape(-1).astype(np.float64, copy=False)
            std_arr = std.reshape(-1).astype(np.float64, copy=False)
            channels = cv2.split(frame)
            pixels = float(frame.shape[0] * frame.shape[1])
            for stat_idx, channel_idx in enumerate(order):
                channel = channels[channel_idx]
                min_val, max_val, _, _ = cv2.minMaxLoc(channel)
                channel_mean = float(mean_arr[channel_idx])
                channel_std = float(std_arr[channel_idx])
                self.sum[stat_idx] += channel_mean * pixels
                self.sumsq[stat_idx] += (
                    channel_std * channel_std
                    + channel_mean * channel_mean
                ) * pixels
                self.min[stat_idx] = min(self.min[stat_idx], float(min_val))
                self.max[stat_idx] = max(self.max[stat_idx], float(max_val))
            return True
        except Exception:
            return False

    def _add_channels_hist(
        self,
        frame: np.ndarray,
        order: tuple[int, int, int],
    ) -> None:
        for stat_idx, channel_idx in enumerate(order):
            channel = frame[:, :, channel_idx]
            hist = np.bincount(channel.reshape(-1), minlength=256)
            self.sum[stat_idx] += float(hist @ _UINT8_VALUES)
            self.sumsq[stat_idx] += float(hist @ _UINT8_SQUARES)
            self.min[stat_idx] = min(self.min[stat_idx], float(channel.min()))
            self.max[stat_idx] = max(self.max[stat_idx], float(channel.max()))

    def to_stats(self) -> Optional[dict[str, Any]]:
        if self.frame_count <= 0 or self.pixel_count <= 0:
            return None
        total = float(self.pixel_count)
        mean_raw = self.sum / total
        variance_raw = np.maximum(self.sumsq / total - mean_raw * mean_raw, 0.0)
        std_raw = np.sqrt(variance_raw)
        mins = self.min / 255.0
        maxs = self.max / 255.0
        means = mean_raw / 255.0
        stds = std_raw / 255.0
        return {
            "min": [[[float(mins[0])]], [[float(mins[1])]], [[float(mins[2])]]],
            "max": [[[float(maxs[0])]], [[float(maxs[1])]], [[float(maxs[2])]]],
            "mean": [
                [[float(means[0])]], [[float(means[1])]], [[float(means[2])]]
            ],
            "std": [[[float(stds[0])]], [[float(stds[1])]], [[float(stds[2])]]],
            "count": [self.frame_count],
        }


def _stats_from_samples(samples_rgb: list[np.ndarray]) -> Optional[dict[str, Any]]:
    stats = _StreamingRgbStats()
    for frame_rgb in samples_rgb:
        stats.add_rgb(frame_rgb)
    return stats.to_stats()


def _video_stats_sample_positions(frame_count: int) -> set[int]:
    """Return output-frame positions to sample for approximate RGB stats."""
    if frame_count <= 0:
        return set()
    raw = os.environ.get(
        _VIDEO_STATS_SAMPLES_ENV,
        str(_DEFAULT_VIDEO_STATS_SAMPLES),
    )
    try:
        sample_count = max(0, int(raw))
    except ValueError:
        sample_count = _DEFAULT_VIDEO_STATS_SAMPLES
    if sample_count <= 0:
        return set()
    return set(
        np.linspace(
            0,
            frame_count - 1,
            min(sample_count, frame_count),
            dtype=int,
        ).tolist()
    )


def _transform_frame_bgr_for_output(
    frame_bgr: np.ndarray,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> np.ndarray:
    """Mirror the ffmpeg filter chain in BGR space for OpenCV writer output."""
    import cv2

    deg = int(rotation_deg or 0) % 360
    if deg == 90:
        frame_bgr = cv2.rotate(frame_bgr, cv2.ROTATE_90_CLOCKWISE)
    elif deg == 180:
        frame_bgr = cv2.rotate(frame_bgr, cv2.ROTATE_180)
    elif deg == 270:
        frame_bgr = cv2.rotate(frame_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if image_resize is not None:
        h, w = int(image_resize[0]), int(image_resize[1])
        if h > 0 and w > 0:
            frame_bgr = cv2.resize(frame_bgr, (w, h), interpolation=cv2.INTER_AREA)
    return frame_bgr


def _transform_frame_for_stats(
    frame_bgr: np.ndarray,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> np.ndarray:
    """Mirror the ffmpeg filter chain closely enough for cached stats."""
    import cv2

    frame_bgr = _transform_frame_bgr_for_output(
        frame_bgr,
        rotation_deg=rotation_deg,
        image_resize=image_resize,
    )
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)


def remux_selected_frames(
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int = 0,
    image_resize: "tuple[int, int] | None" = None,
) -> VideoSyncResult:
    """Produce ``output_mp4`` containing the listed frames in order.

    Args:
        input_mp4: source MJPEG-in-MP4 written by VideoRecorder.
        frame_indices: 0-based indices into ``input_mp4`` selected for
            output (one per target grid step). Repeats are allowed and
            simply duplicate the source frame in the output.
        output_mp4: destination path.
        target_fps: container framerate stamped on the output. Each
            entry of ``frame_indices`` becomes one frame at
            ``1/target_fps``.
    """
    input_mp4 = Path(input_mp4)
    output_mp4 = Path(output_mp4)
    output_mp4.parent.mkdir(parents=True, exist_ok=True)

    if not input_mp4.exists():
        raise FileNotFoundError(input_mp4)
    if len(frame_indices) == 0:
        raise ValueError("frame_indices is empty")

    frame_indices = [int(i) for i in frame_indices]
    ffmpeg = _ffmpeg()
    if not os.environ.get(_VIDEO_SYNC_FORCE_FALLBACK_ENV):
        if _can_packet_copy_sync(
            input_mp4=input_mp4,
            frame_indices=frame_indices,
            target_fps=target_fps,
            rotation_deg=rotation_deg,
            image_resize=image_resize,
            ffmpeg=ffmpeg,
        ):
            try:
                return _remux_selected_frames_packet_copy(
                    input_mp4=input_mp4,
                    frame_indices=frame_indices,
                    output_mp4=output_mp4,
                    target_fps=target_fps,
                    ffmpeg=ffmpeg,
                )
            except Exception as exc:
                import sys as _sys
                print(
                    f"video_sync[{input_mp4.name}]: packet-copy path failed "
                    f"({exc!r}); falling back to streaming encode",
                    file=_sys.stderr,
                )
                output_mp4.unlink(missing_ok=True)
        try:
            return _remux_selected_frames_streaming(
                input_mp4=input_mp4,
                frame_indices=frame_indices,
                output_mp4=output_mp4,
                target_fps=target_fps,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
                ffmpeg=ffmpeg,
            )
        except Exception as exc:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: streaming path failed "
                f"({exc!r}); falling back to JPEG temp path",
                file=_sys.stderr,
            )
            output_mp4.unlink(missing_ok=True)

    tmp_parent, cleanup_tmp_parent = _resolve_tmp_parent(output_mp4)
    try:
        _check_tmp_free_space(tmp_parent)
        with tempfile.TemporaryDirectory(
            prefix="video_sync_", dir=str(tmp_parent)
        ) as tmpdir:
            tmp = Path(tmpdir)
            frames_dir = tmp / "frames"
            frames_dir.mkdir()
            seq_dir = tmp / "seq"
            seq_dir.mkdir()

            _remux_selected_frames_in_tmp(
                input_mp4=input_mp4,
                frame_indices=frame_indices,
                output_mp4=output_mp4,
                target_fps=target_fps,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
                ffmpeg=ffmpeg,
                frames_dir=frames_dir,
                seq_dir=seq_dir,
            )
        produced_frames = _video_frame_count(output_mp4)
        if produced_frames != len(frame_indices):
            output_mp4.unlink(missing_ok=True)
            raise RuntimeError(
                f"video_sync fallback frame count mismatch for "
                f"{output_mp4.name}: expected {len(frame_indices)}, "
                f"got {produced_frames}"
            )
        return VideoSyncResult(
            frame_count=int(produced_frames),
            stats=None,
            used_fallback=True,
            mode="jpeg_fallback",
        )
    finally:
        _cleanup_tmp_parent(tmp_parent, cleanup_tmp_parent)


def _remux_selected_frames_streaming(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Decode input once and stream selected BGR frames to ffmpeg."""
    if not os.environ.get(_VIDEO_SYNC_DISABLE_YUV420_PIPE_ENV):
        try:
            return _remux_selected_frames_ffmpeg_yuv420_pipe(
                input_mp4=input_mp4,
                frame_indices=frame_indices,
                output_mp4=output_mp4,
                target_fps=target_fps,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
                ffmpeg=ffmpeg,
            )
        except Exception as exc:
            import sys as _sys
            if "YUV420 pipe is not eligible" not in str(exc):
                print(
                    f"video_sync[{input_mp4.name}]: YUV420 ffmpeg pipe path failed "
                    f"({exc!r}); falling back to OpenCV stream path",
                    file=_sys.stderr,
                )
            output_mp4.unlink(missing_ok=True)

    if os.environ.get(_VIDEO_SYNC_ENABLE_FFMPEG_PIPE_ENV):
        try:
            return _remux_selected_frames_ffmpeg_pipe(
                input_mp4=input_mp4,
                frame_indices=frame_indices,
                output_mp4=output_mp4,
                target_fps=target_fps,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
                ffmpeg=ffmpeg,
            )
        except Exception as exc:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: ffmpeg-pipe stream path failed "
                f"({exc!r}); falling back to OpenCV stream path",
                file=_sys.stderr,
            )
            output_mp4.unlink(missing_ok=True)

    return _remux_selected_frames_opencv_streaming(
        input_mp4=input_mp4,
        frame_indices=frame_indices,
        output_mp4=output_mp4,
        target_fps=target_fps,
        rotation_deg=rotation_deg,
        image_resize=image_resize,
        ffmpeg=ffmpeg,
    )


def _read_exact(pipe, size: int) -> bytes:
    data = pipe.read(size)
    if len(data) == size or not data:
        return data
    chunks: list[bytes] = []
    remaining = size - len(data)
    chunks.append(data)
    while remaining > 0:
        chunk = pipe.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_exact_into(pipe, buffer: bytearray, size: int) -> int:
    """Read up to ``size`` bytes into ``buffer`` and return bytes read."""
    view = memoryview(buffer)
    total = 0
    while total < size:
        try:
            n = pipe.readinto(view[total:size])
        except AttributeError:
            data = _read_exact(pipe, size - total)
            n = len(data)
            if n:
                view[total:total + n] = data
        if not n:
            break
        total += int(n)
    return total


def _splice_exact(src_pipe, dst_pipe, size: int) -> int:
    """Move up to ``size`` bytes between pipe FDs without userspace copy."""
    try:
        splice = os.splice
        src_fd = src_pipe.fileno()
        dst_fd = dst_pipe.fileno()
    except Exception:
        return 0
    total = 0
    while total < size:
        try:
            n = splice(src_fd, dst_fd, size - total)
        except OSError:
            if total == 0:
                return 0
            raise
        if not n:
            break
        total += int(n)
    return total


def _contiguous_forward_run_length(
    indices: Sequence[int],
    start: int,
) -> int:
    """Return how many indices from ``start`` increase by exactly one."""
    total = len(indices)
    if start < 0 or start >= total:
        return 0
    count = 1
    previous = int(indices[start])
    idx = start + 1
    while idx < total:
        current = int(indices[idx])
        if current != previous + 1:
            break
        if idx + 1 < total and int(indices[idx + 1]) == current:
            break
        count += 1
        previous = current
        idx += 1
    return count


def _drain_exact(
    pipe,
    size: int,
    *,
    discard_fd: Optional[int] = None,
    buffer: Optional[bytearray] = None,
) -> int:
    """Discard up to ``size`` bytes from a pipe and return bytes drained."""
    if size <= 0:
        return 0

    total = 0
    if discard_fd is not None:
        try:
            splice = os.splice
            src_fd = pipe.fileno()
        except Exception:
            pass
        else:
            while total < size:
                try:
                    n = splice(src_fd, discard_fd, size - total)
                except OSError:
                    if total == 0:
                        break
                    raise
                if not n:
                    return total
                total += int(n)
            if total == size:
                return total

    if buffer is None:
        buffer = bytearray(min(size, 1024 * 1024))
    view = memoryview(buffer)
    while total < size:
        chunk_size = min(len(buffer), size - total)
        try:
            n = pipe.readinto(view[:chunk_size])
        except AttributeError:
            data = _read_exact(pipe, chunk_size)
            n = len(data)
        if not n:
            break
        total += int(n)
    return total


def _add_frame_bgr_for_stats(
    stats: _StreamingRgbStats,
    frame_bgr: np.ndarray,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> None:
    if rotation_deg or image_resize is not None:
        stats.add_rgb(
            _transform_frame_for_stats(
                frame_bgr,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
            )
        )
    else:
        stats.add_bgr(frame_bgr)


def _add_frame_bytes_for_stats(
    stats: _StreamingRgbStats,
    frame_bgr_bytes: bytes,
    width: int,
    height: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> None:
    frame = np.frombuffer(frame_bgr_bytes, dtype=np.uint8).reshape(
        (height, width, 3)
    )
    if rotation_deg or image_resize is not None:
        stats.add_rgb(
            _transform_frame_for_stats(
                frame.copy(),
                rotation_deg=rotation_deg,
                image_resize=image_resize,
            )
        )
    else:
        stats.add_bgr(frame)


def _add_frame_yuv420p_for_stats(
    stats: _StreamingRgbStats,
    frame_yuv420p_bytes: bytes,
    width: int,
    height: int,
) -> None:
    """Convert a sampled I420 frame only when stats actually need it."""
    import cv2

    frame_yuv = np.frombuffer(frame_yuv420p_bytes, dtype=np.uint8).reshape(
        (height * 3 // 2, width)
    )
    frame_bgr = cv2.cvtColor(frame_yuv, cv2.COLOR_YUV2BGR_I420)
    stats.add_bgr(frame_bgr)


def _write_frame_bgr(pipe, frame_bgr: np.ndarray) -> None:
    """Write one OpenCV BGR frame to a rawvideo pipe with minimal copying."""
    if not frame_bgr.flags.c_contiguous:
        frame_bgr = np.ascontiguousarray(frame_bgr)
    pipe.write(memoryview(frame_bgr))


def _use_yuv420_pipe(
    width: int,
    height: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
) -> bool:
    """Return True when OpenCV can feed ffmpeg YUV420p rawvideo directly."""
    if os.environ.get(_VIDEO_SYNC_DISABLE_YUV420_PIPE_ENV):
        return False
    if rotation_deg or image_resize is not None:
        return False
    return width > 0 and height > 0 and width % 2 == 0 and height % 2 == 0


def _frame_bgr_to_yuv420p(frame_bgr: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YUV_I420)


def _write_frame_yuv420p(pipe, frame_yuv420p: np.ndarray) -> None:
    if not frame_yuv420p.flags.c_contiguous:
        frame_yuv420p = np.ascontiguousarray(frame_yuv420p)
    pipe.write(memoryview(frame_yuv420p))


def _write_frame_bytes(pipe, frame_bytes) -> None:
    try:
        fd = pipe.fileno()
    except Exception:
        pipe.write(frame_bytes)
        return
    view = memoryview(frame_bytes)
    total = 0
    size = len(view)
    while total < size:
        n = os.write(fd, view[total:])
        if not n:
            raise BrokenPipeError("short write to ffmpeg stdin")
        total += int(n)


@lru_cache(maxsize=1)
def _writev_iov_batch_limit() -> int:
    try:
        iov_max = int(os.sysconf("SC_IOV_MAX"))
    except (OSError, ValueError):
        iov_max = 16
    return max(1, min(iov_max, 16))


def _write_repeated_frame_bytes(pipe, frame_bytes, count: int) -> None:
    """Write the same raw frame ``count`` times, using writev when available."""
    count = int(count)
    if count <= 0:
        return
    if count == 1:
        _write_frame_bytes(pipe, frame_bytes)
        return
    try:
        fd = pipe.fileno()
        writev = os.writev
    except Exception:
        for _ in range(count):
            pipe.write(frame_bytes)
        return

    view = memoryview(frame_bytes)
    frame_size = len(view)
    if frame_size <= 0:
        return
    batch_limit = _writev_iov_batch_limit()
    remaining = count
    offset = 0
    while remaining > 0:
        batch_count = min(remaining, batch_limit)
        if offset:
            buffers = [view[offset:]]
            if batch_count > 1:
                buffers.extend([view] * (batch_count - 1))
        else:
            buffers = [view] * batch_count
        written = writev(fd, buffers)
        if not written:
            raise BrokenPipeError("short write to ffmpeg stdin")

        n = int(written)
        if offset:
            first_remaining = frame_size - offset
            if n < first_remaining:
                offset += n
                continue
            n -= first_remaining
            remaining -= 1
            offset = 0

        full_frames, partial = divmod(n, frame_size)
        remaining -= int(full_frames)
        offset = int(partial)


def _advance_capture_to_index(
    cap,
    *,
    current_idx: int,
    requested_idx: int,
    last_frame: "np.ndarray | None",
    input_name: str,
) -> tuple[int, "np.ndarray | None", bool]:
    """Advance OpenCV capture to requested index, grabbing skipped frames."""
    clamped = False
    while current_idx < requested_idx:
        next_idx = current_idx + 1
        if next_idx < requested_idx:
            if not cap.grab():
                if last_frame is None:
                    raise RuntimeError(f"no frames decoded from {input_name}")
                clamped = True
                break
            current_idx = next_idx
            continue

        ret, frame = cap.read()
        if not ret:
            if last_frame is None:
                raise RuntimeError(f"no frames decoded from {input_name}")
            clamped = True
            break
        current_idx = next_idx
        last_frame = frame
    return current_idx, last_frame, clamped


def _validate_stream_encoded_output(
    output_mp4: Path,
    expected_frames: int,
    target_fps: int,
    ffmpeg: str,
) -> int:
    return _validated_video_count(
        output_mp4=output_mp4,
        expected_frames=expected_frames,
        target_fps=target_fps,
        ffmpeg=ffmpeg,
        label="streaming",
    )


def _remux_selected_frames_ffmpeg_yuv420_pipe(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Decode to I420 with ffmpeg and duplicate selected raw frames in Python."""
    if any(i < 0 for i in frame_indices):
        raise ValueError("frame_indices must be non-negative")
    if any(b < a for a, b in zip(frame_indices, frame_indices[1:])):
        raise ValueError("streaming sync requires non-decreasing frame_indices")

    width, height = _quick_video_dimensions(input_mp4)
    if not _use_yuv420_pipe(width, height, rotation_deg, image_resize):
        raise RuntimeError("YUV420 pipe is not eligible for this video")

    frame_size = width * height * 3 // 2
    decoder_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        *_ffmpeg_h264_decoder_args(ffmpeg),
        "-i", str(input_mp4),
        "-map", "0:v:0",
        "-an",
        "-fps_mode", "passthrough",
        "-f", "rawvideo",
        "-pix_fmt", "yuv420p",
        "pipe:1",
    ]
    encoder, enc_opts = _h264_encoder(
        ffmpeg,
        width=width,
        height=height,
        allow_gstreamer=True,
    )
    if _is_gstreamer_encoder(encoder):
        encoder_cmd = _gstreamer_h264_command(
            input_pix_fmt="yuv420p",
            width=width,
            height=height,
            fps=int(target_fps),
            output_mp4=output_mp4,
            opts=enc_opts,
        )
    else:
        encoder_cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            *_ffmpeg_threads_arg(),
            "-f", "rawvideo",
            "-pix_fmt", "yuv420p",
            "-s", f"{width}x{height}",
            "-r", str(int(target_fps)),
            "-i", "pipe:0",
            "-c:v", encoder,
            *enc_opts,
            "-pix_fmt", "yuv420p",
            "-r", str(int(target_fps)),
            "-video_track_timescale", "90000",
            str(output_mp4),
        ]

    decoder: subprocess.Popen | None = None
    encoder_process: subprocess.Popen | None = None
    last_frame: bytes | None = None
    current_idx = -1
    clamped_count = 0
    sample_positions = _video_stats_sample_positions(len(frame_indices))
    stats = _StreamingRgbStats()
    try:
        decoder = subprocess.Popen(
            decoder_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        encoder_process = subprocess.Popen(
            encoder_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=_gstreamer_env() if _is_gstreamer_encoder(encoder) else None,
        )
        assert decoder.stdout is not None
        assert encoder_process.stdin is not None

        for out_idx, requested_idx in enumerate(frame_indices):
            while current_idx < requested_idx:
                frame = _read_exact(decoder.stdout, frame_size)
                if len(frame) != frame_size:
                    if last_frame is None:
                        raise RuntimeError(
                            f"no frames decoded from {input_mp4.name}"
                        )
                    clamped_count += 1
                    break
                current_idx += 1
                last_frame = frame

            if last_frame is None:
                raise RuntimeError(f"no frame available for {input_mp4.name}")

            _write_frame_bytes(encoder_process.stdin, last_frame)
            if out_idx in sample_positions:
                _add_frame_yuv420p_for_stats(
                    stats,
                    last_frame,
                    width=width,
                    height=height,
                )

        encoder_process.stdin.close()
        stderr = (
            encoder_process.stderr.read().decode(errors="replace")
            if encoder_process.stderr is not None else ""
        )
        rc = encoder_process.wait(timeout=300)
        if rc != 0:
            label = "gstreamer encode" if _is_gstreamer_encoder(encoder) else "ffmpeg encode"
            raise RuntimeError(f"{label} rc={rc}: {stderr[-500:]}")

        produced_frames = _validate_stream_encoded_output(
            output_mp4,
            expected_frames=len(frame_indices),
            target_fps=target_fps,
            ffmpeg=ffmpeg,
        )

        if clamped_count:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: clamped {clamped_count} "
                f"selected indices beyond decoded frames to last frame",
                file=_sys.stderr,
            )

        return VideoSyncResult(
            frame_count=int(produced_frames),
            stats=stats.to_stats(),
            used_fallback=False,
            mode="gstreamer_encode" if _is_gstreamer_encoder(encoder) else "stream_encode",
            output_height=height,
            output_width=width,
        )
    finally:
        if decoder is not None and decoder.poll() is None:
            decoder.kill()
        if encoder_process is not None and encoder_process.poll() is None:
            try:
                if encoder_process.stdin and not encoder_process.stdin.closed:
                    encoder_process.stdin.close()
            except Exception:
                pass
            encoder_process.kill()


def _remux_selected_frames_ffmpeg_pipe(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Use ffmpeg for decode and encode, with Python only selecting frames."""
    if any(i < 0 for i in frame_indices):
        raise ValueError("frame_indices must be non-negative")
    if any(b < a for a, b in zip(frame_indices, frame_indices[1:])):
        raise ValueError("streaming sync requires non-decreasing frame_indices")

    stream = _probe_video_stream(input_mp4)
    if not stream:
        raise RuntimeError(f"failed to probe input video stream: {input_mp4}")
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"invalid video dimensions for {input_mp4}: {width}x{height}")

    frame_size = width * height * 3
    decoder_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        *_ffmpeg_h264_decoder_args(ffmpeg),
        "-i", str(input_mp4),
        "-map", "0:v:0",
        "-an",
        "-fps_mode", "passthrough",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    encoder, enc_opts = _h264_encoder(
        ffmpeg,
        width=width,
        height=height,
        allow_gstreamer=True,
    )
    if _is_gstreamer_encoder(encoder) and not rotation_deg and image_resize is None:
        encoder_cmd = _gstreamer_h264_command(
            input_pix_fmt="bgr24",
            width=width,
            height=height,
            fps=int(target_fps),
            output_mp4=output_mp4,
            opts=enc_opts,
        )
    else:
        if _is_gstreamer_encoder(encoder):
            encoder, enc_opts = _libx264_encoder()
        encoder_cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            *_ffmpeg_threads_arg(),
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}",
            "-r", str(int(target_fps)),
            "-i", "pipe:0",
            *_video_filter_args(rotation_deg, image_resize),
            "-c:v", encoder,
            *enc_opts,
            "-pix_fmt", "yuv420p",
            "-r", str(int(target_fps)),
            "-video_track_timescale", "90000",
            str(output_mp4),
        ]

    decoder: subprocess.Popen | None = None
    encoder_process: subprocess.Popen | None = None
    last_frame: bytes | None = None
    current_idx = -1
    clamped_count = 0
    sample_positions = _video_stats_sample_positions(len(frame_indices))
    stats = _StreamingRgbStats()
    try:
        decoder = subprocess.Popen(
            decoder_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        encoder_process = subprocess.Popen(
            encoder_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=_gstreamer_env() if _is_gstreamer_encoder(encoder) else None,
        )
        assert decoder.stdout is not None
        assert encoder_process.stdin is not None

        for out_idx, requested_idx in enumerate(frame_indices):
            while current_idx < requested_idx:
                frame = _read_exact(decoder.stdout, frame_size)
                if len(frame) != frame_size:
                    if last_frame is None:
                        raise RuntimeError(
                            f"no frames decoded from {input_mp4.name}"
                        )
                    clamped_count += 1
                    break
                current_idx += 1
                last_frame = frame

            if last_frame is None:
                raise RuntimeError(f"no frame available for {input_mp4.name}")

            encoder_process.stdin.write(last_frame)
            if out_idx in sample_positions:
                _add_frame_bytes_for_stats(
                    stats,
                    last_frame,
                    width=width,
                    height=height,
                    rotation_deg=rotation_deg,
                    image_resize=image_resize,
                )

        encoder_process.stdin.close()
        stderr = (
            encoder_process.stderr.read().decode(errors="replace")
            if encoder_process.stderr is not None else ""
        )
        rc = encoder_process.wait(timeout=300)
        if rc != 0:
            label = "gstreamer encode" if _is_gstreamer_encoder(encoder) else "ffmpeg encode"
            raise RuntimeError(f"{label} rc={rc}: {stderr[-500:]}")

        produced_frames = _validate_stream_encoded_output(
            output_mp4,
            expected_frames=len(frame_indices),
            target_fps=target_fps,
            ffmpeg=ffmpeg,
        )

        if clamped_count:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: clamped {clamped_count} "
                f"selected indices beyond decoded frames to last frame",
                file=_sys.stderr,
            )

        output_height, output_width = _output_dimensions(
            height, width, rotation_deg, image_resize
        )
        return VideoSyncResult(
            frame_count=int(produced_frames),
            stats=stats.to_stats(),
            used_fallback=False,
            mode="gstreamer_encode" if _is_gstreamer_encoder(encoder) else "stream_encode",
            output_height=output_height,
            output_width=output_width,
        )
    finally:
        if decoder is not None and decoder.poll() is None:
            decoder.kill()
        if encoder_process is not None and encoder_process.poll() is None:
            try:
                if encoder_process.stdin and not encoder_process.stdin.closed:
                    encoder_process.stdin.close()
            except Exception:
                pass
            encoder_process.kill()


def _remux_selected_frames_opencv_streaming(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Decode input with OpenCV and write selected BGR frames to H.264."""
    if (
        os.environ.get(_VIDEO_SYNC_ENABLE_OPENCV_WRITER_ENV)
        and not os.environ.get(_VIDEO_SYNC_DISABLE_OPENCV_WRITER_ENV)
    ):
        try:
            return _remux_selected_frames_opencv_writer(
                input_mp4=input_mp4,
                frame_indices=frame_indices,
                output_mp4=output_mp4,
                target_fps=target_fps,
                rotation_deg=rotation_deg,
                image_resize=image_resize,
                ffmpeg=ffmpeg,
            )
        except Exception as exc:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: OpenCV writer path failed "
                f"({exc!r}); falling back to ffmpeg rawvideo encoder",
                file=_sys.stderr,
            )
            output_mp4.unlink(missing_ok=True)

    return _remux_selected_frames_opencv_ffmpeg_encoder(
        input_mp4=input_mp4,
        frame_indices=frame_indices,
        output_mp4=output_mp4,
        target_fps=target_fps,
        rotation_deg=rotation_deg,
        image_resize=image_resize,
        ffmpeg=ffmpeg,
    )


def _remux_selected_frames_opencv_writer(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Decode and encode with OpenCV's FFmpeg-backed H.264 writer."""
    if any(i < 0 for i in frame_indices):
        raise ValueError("frame_indices must be non-negative")
    if any(b < a for a, b in zip(frame_indices, frame_indices[1:])):
        raise ValueError("streaming sync requires non-decreasing frame_indices")

    import cv2

    cap = cv2.VideoCapture(str(input_mp4))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open input video: {input_mp4}")

    writer = None
    output_height: int | None = None
    output_width: int | None = None
    last_frame: np.ndarray | None = None
    current_idx = -1
    clamped_count = 0
    sample_positions = _video_stats_sample_positions(len(frame_indices))
    stats = _StreamingRgbStats()

    try:
        for out_idx, requested_idx in enumerate(frame_indices):
            current_idx, last_frame, clamped = _advance_capture_to_index(
                cap,
                current_idx=current_idx,
                requested_idx=requested_idx,
                last_frame=last_frame,
                input_name=input_mp4.name,
            )
            if clamped:
                clamped_count += 1

            if last_frame is None:
                raise RuntimeError(f"no frame available for {input_mp4.name}")

            output_frame = (
                _transform_frame_bgr_for_output(
                    last_frame,
                    rotation_deg=rotation_deg,
                    image_resize=image_resize,
                )
                if rotation_deg or image_resize is not None
                else last_frame
            )

            if writer is None:
                height, width = output_frame.shape[:2]
                output_height, output_width = int(height), int(width)
                writer = cv2.VideoWriter(
                    str(output_mp4),
                    cv2.VideoWriter_fourcc(*"avc1"),
                    float(target_fps),
                    (width, height),
                )
                if not writer.isOpened():
                    raise RuntimeError("OpenCV H.264 VideoWriter failed to open")

            writer.write(output_frame)

            if out_idx in sample_positions:
                stats.add_bgr(output_frame)

        if writer is None:
            raise RuntimeError("OpenCV writer was not started")
        writer.release()
        writer = None

        produced_frames = _validate_stream_encoded_output(
            output_mp4,
            expected_frames=len(frame_indices),
            target_fps=target_fps,
            ffmpeg=ffmpeg,
        )

        if clamped_count:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: clamped {clamped_count} "
                f"selected indices beyond decoded frames to last frame",
                file=_sys.stderr,
            )

        return VideoSyncResult(
            frame_count=produced_frames,
            stats=stats.to_stats(),
            used_fallback=False,
            mode="stream_encode",
            output_height=output_height,
            output_width=output_width,
        )
    finally:
        cap.release()
        if writer is not None:
            writer.release()


def _remux_selected_frames_opencv_ffmpeg_encoder(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
) -> VideoSyncResult:
    """Decode input with OpenCV and stream selected BGR frames to ffmpeg."""
    if any(i < 0 for i in frame_indices):
        raise ValueError("frame_indices must be non-negative")
    if any(b < a for a, b in zip(frame_indices, frame_indices[1:])):
        raise ValueError("streaming sync requires non-decreasing frame_indices")

    import cv2

    cap = cv2.VideoCapture(str(input_mp4))
    if not cap.isOpened():
        raise RuntimeError(f"failed to open input video: {input_mp4}")

    process: subprocess.Popen | None = None
    last_frame: np.ndarray | None = None
    last_yuv420p: np.ndarray | None = None
    last_yuv420p_idx: int | None = None
    current_idx = -1
    clamped_count = 0
    sample_positions = _video_stats_sample_positions(len(frame_indices))
    stats = _StreamingRgbStats()
    stderr = ""
    use_yuv420_pipe = False
    output_height: int | None = None
    output_width: int | None = None

    try:
        for out_idx, requested_idx in enumerate(frame_indices):
            current_idx, last_frame, clamped = _advance_capture_to_index(
                cap,
                current_idx=current_idx,
                requested_idx=requested_idx,
                last_frame=last_frame,
                input_name=input_mp4.name,
            )
            if clamped:
                clamped_count += 1

            if last_frame is None:
                raise RuntimeError(f"no frame available for {input_mp4.name}")

            if process is None:
                height, width = last_frame.shape[:2]
                output_height, output_width = _output_dimensions(
                    height,
                    width,
                    rotation_deg,
                    image_resize,
                )
                use_yuv420_pipe = _use_yuv420_pipe(
                    width,
                    height,
                    rotation_deg,
                    image_resize,
                )
                input_pix_fmt = "yuv420p" if use_yuv420_pipe else "bgr24"
                encoder, enc_opts = _h264_encoder(
                    ffmpeg,
                    width=width,
                    height=height,
                )
                cmd = [
                    ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
                    *_ffmpeg_threads_arg(),
                    "-f", "rawvideo",
                    "-pix_fmt", input_pix_fmt,
                    "-s", f"{width}x{height}",
                    "-r", str(int(target_fps)),
                    "-i", "pipe:0",
                    *_video_filter_args(rotation_deg, image_resize),
                    "-c:v", encoder,
                    *enc_opts,
                    "-pix_fmt", "yuv420p",
                    "-r", str(int(target_fps)),
                    "-video_track_timescale", "90000",
                    str(output_mp4),
                ]
                process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )

            assert process.stdin is not None
            if use_yuv420_pipe:
                if last_yuv420p_idx != current_idx:
                    last_yuv420p = _frame_bgr_to_yuv420p(last_frame)
                    last_yuv420p_idx = current_idx
                assert last_yuv420p is not None
                _write_frame_yuv420p(process.stdin, last_yuv420p)
            else:
                _write_frame_bgr(process.stdin, last_frame)

            if out_idx in sample_positions:
                _add_frame_bgr_for_stats(
                    stats,
                    last_frame,
                    rotation_deg=rotation_deg,
                    image_resize=image_resize,
                )

        if process is None or process.stdin is None:
            raise RuntimeError("encoder was not started")
        process.stdin.close()
        if process.stderr is not None:
            stderr = process.stderr.read().decode(errors="replace")
        rc = process.wait(timeout=300)
        if rc != 0:
            raise RuntimeError(f"ffmpeg encode rc={rc}: {stderr[-500:]}")

        produced_frames = _validate_stream_encoded_output(
            output_mp4,
            expected_frames=len(frame_indices),
            target_fps=target_fps,
            ffmpeg=ffmpeg,
        )

        if clamped_count:
            import sys as _sys
            print(
                f"video_sync[{input_mp4.name}]: clamped {clamped_count} "
                f"selected indices beyond decoded frames to last frame",
                file=_sys.stderr,
            )

        return VideoSyncResult(
            frame_count=int(produced_frames),
            stats=stats.to_stats(),
            used_fallback=False,
            mode="stream_encode",
            output_height=output_height,
            output_width=output_width,
        )
    finally:
        cap.release()
        if process is not None and process.poll() is None:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except Exception:
                pass
            process.kill()


def _remux_selected_frames_in_tmp(
    *,
    input_mp4: Path,
    frame_indices: Sequence[int],
    output_mp4: Path,
    target_fps: int,
    rotation_deg: int,
    image_resize: "tuple[int, int] | None",
    ffmpeg: str,
    frames_dir: Path,
    seq_dir: Path,
) -> None:
    # 1) Extract input frames as JPEGs.
    #
    # Fast path (MJPEG source — pre-transcode recordings): copy
    # each packet's JPEG payload verbatim, no decode/encode, no
    # quality loss.
    #
    # Generic path (H.264 source — post-transcode recordings, or
    # anything else): decode and re-encode each frame as a high-
    # quality JPEG. Slightly slower and not bit-exact, but works
    # for any codec the recorder might produce.
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0",
            str(input_mp4),
        ],
        capture_output=True, text=True, timeout=30,
    )
    src_codec = probe.stdout.strip()

    if src_codec == "mjpeg":
        # ``-c:v copy`` doesn't decode so threads matter little, but
        # we pass the flag anyway for consistency / future codec
        # changes.
        extract_cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            *_ffmpeg_threads_arg(),
            "-i", str(input_mp4),
            "-c:v", "copy",
            "-fps_mode", "passthrough",
            "-f", "image2",
            "-start_number", "0",
            str(frames_dir / "f_%08d.jpg"),
        ]
    else:
        # H.264 source: ffmpeg decodes here, so the auto-thread default
        # (= cpu_count) blows up to 6+ cores per ffmpeg without the
        # cap. Observed 264% CPU per extract before the cap.
        #
        # CRITICAL: image selection is index-based, so we need exactly
        # one JPG per decoded input frame. ``-fps_mode passthrough``
        # avoids CFR duplication from stale container r_frame_rate tags,
        # while ``setpts=N/TB`` regenerates monotonic output PTS for
        # H.264 files whose source PTS/DTS are duplicated or slightly
        # out of order. Without the setpts filter, ffmpeg's image2 muxer
        # can fail with "non monotonically increasing dts" before all
        # frames are extracted.
        extract_cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            *_ffmpeg_threads_arg(),
            "-i", str(input_mp4),
            "-vf", "setpts=N/TB",
            "-q:v", "2",
            "-fps_mode", "passthrough",
            "-f", "image2",
            "-start_number", "0",
            str(frames_dir / "f_%08d.jpg"),
        ]
    subprocess.run(extract_cmd, check=True)

    # The sidecar parquet can list more rows than the MP4 actually
    # contains: ffmpeg's mjpeg demuxer drops the first packet when
    # it lacks an EOI marker (some camera drivers emit truncated
    # initial frames), so a 95-row sidecar maps onto a 94-frame
    # MP4. Clamp selected indices to the actually-extracted range
    # rather than failing — the lost frame would have been the very
    # first one which is rarely a useful anchor anyway.
    extracted_files = sorted(frames_dir.glob("f_*.jpg"))
    if not extracted_files:
        raise RuntimeError(
            f"No frames extracted from {input_mp4.name}; cannot remux"
        )
    extracted_count = len(extracted_files)
    max_idx = max(frame_indices)
    if max_idx >= extracted_count:
        # Clamp every out-of-range index to the last available frame.
        clamped_indices = [
            min(i, extracted_count - 1) for i in frame_indices
        ]
        clamped_count = sum(
            1 for i, c in zip(frame_indices, clamped_indices) if i != c
        )
        # We can't log via a node logger from here (this module is
        # also called from worker processes), so just emit a warning
        # to stderr so the caller's log capture picks it up.
        import sys as _sys
        print(
            f"video_sync[{input_mp4.name}]: clamped {clamped_count} "
            f"selected indices >= {extracted_count} to last frame "
            f"(sidecar/MP4 mismatch)",
            file=_sys.stderr,
        )
        frame_indices = clamped_indices  # type: ignore[assignment]

    # 2) Hard-link selected frames into a fresh sequence so we can
    #    drive ffmpeg's image2 demuxer (sequential, gap-free) and
    #    sidestep concat demuxer's duration quirks. Hard links cost
    #    no extra disk and survive across the second ffmpeg run.
    for out_idx, src_idx in enumerate(frame_indices):
        src = frames_dir / f"f_{src_idx:08d}.jpg"
        dst = seq_dir / f"seq_{out_idx:08d}.jpg"
        try:
            os.link(src, dst)
        except OSError:
            # Fall back to copy if the temp dir is on a filesystem
            # that doesn't support hard links (rare).
            shutil.copy2(src, dst)

    # 3) Re-encode into H.264 MP4 at exactly target_fps. The recorder
    #    keeps frames as raw MJPEG to avoid live CPU/GPU load; the
    #    convert step is the right place to pay for H.264 so the
    #    final LeRobot dataset is universally playable (Chromium /
    #    VSCode webview / browser <video> require H.264). HW encoder
    #    (NVENC / v4l2m2m) is preferred when available.
    # ``-video_track_timescale 90000`` pins the MP4 timebase to a
    # value that exactly represents 1/15s, 1/30s, 1/60s, etc. The
    # ffmpeg default (1/1000) truncates 1/15s = 0.0666...s to 66ms,
    # which makes ffprobe report avg_frame_rate=15.1515 instead of
    # 15. 90000 is the H.264 RTP standard and divides cleanly into
    # every common LeRobot fps.
    encoder_width: int | None = None
    encoder_height: int | None = None
    if image_resize is not None:
        encoder_height = int(image_resize[0])
        encoder_width = int(image_resize[1])
    else:
        stream = _probe_video_stream(input_mp4)
        if stream:
            encoder_width = int(stream.get("width") or 0) or None
            encoder_height = int(stream.get("height") or 0) or None

    encoder, enc_opts = _h264_encoder(
        ffmpeg,
        width=encoder_width,
        height=encoder_height,
    )
    # Build the ``-vf`` chain. Order matters: rotation first (because
    # transpose changes width/height), then scale so the user's
    # target dimensions describe the final output, not the
    # pre-rotation orientation. ``image_resize`` is ``(height, width)``
    # to match the orchestrator-side ConversionConfig field.
    mux_cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        *_ffmpeg_threads_arg(),
        "-framerate", str(int(target_fps)),
        "-start_number", "0",
        "-i", str(seq_dir / "seq_%08d.jpg"),
        *_video_filter_args(rotation_deg, image_resize),
        "-c:v", encoder,
        *enc_opts,
        "-pix_fmt", "yuv420p",
        "-r", str(int(target_fps)),
        "-video_track_timescale", "90000",
        str(output_mp4),
    ]
    subprocess.run(mux_cmd, check=True)

    # Verify the encoder actually produced a non-empty file.
    # ffmpeg occasionally returns rc=0 with a 0-byte output (e.g.
    # the older "extract MJPEG packets from H.264 source" bug) and
    # we must not silently ship that to the LeRobot dataset.
    if not output_mp4.exists() or output_mp4.stat().st_size == 0:
        output_mp4.unlink(missing_ok=True)
        raise RuntimeError(
            f"video_sync produced an empty file for {output_mp4.name}; "
            f"check input codec ({src_codec}) compatibility"
        )
