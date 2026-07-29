from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

MAX_FFMPEG_STDERR_BYTES = 64 * 1024
VIDEO_TRIM_TIMEOUT_SECONDS = 900.0


class VideoTrimError(RuntimeError):
    pass


class VideoTrimUnavailableError(VideoTrimError):
    pass


class VideoTrimBusyError(VideoTrimError):
    pass


class VideoTrimTimeoutError(VideoTrimError):
    pass


class VideoTrimProcessError(VideoTrimError):
    def __init__(self, message: str, *, stderr: bytes = b"") -> None:
        super().__init__(message)
        self.stderr = stderr[:MAX_FFMPEG_STDERR_BYTES]


class VideoTrimValidationError(VideoTrimError, ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VideoTrimProcessResult:
    returncode: int
    stderr: bytes
    stderr_truncated: bool


def build_video_trim_command(
    executable: str,
    source_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    end_seconds: float,
) -> tuple[str, ...]:
    duration_seconds = end_seconds - start_seconds
    return (
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-xerror",
        "-i",
        str(source_path),
        "-ss",
        _ffmpeg_seconds(start_seconds),
        "-t",
        _ffmpeg_seconds(duration_seconds),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-map_metadata",
        "-1",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-avoid_negative_ts",
        "make_zero",
        "-y",
        str(output_path),
    )


def run_bounded_process(
    command: tuple[str, ...],
    timeout_seconds: float,
) -> VideoTrimProcessResult:
    """Run one fixed argv while draining stderr without retaining unbounded output."""
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creation_flags,
        )
    except FileNotFoundError as exc:
        raise VideoTrimUnavailableError("FFmpeg is not available.") from exc
    except OSError as exc:
        raise VideoTrimProcessError("FFmpeg could not be started.") from exc

    retained = bytearray()
    stderr_truncated = False

    def drain_stderr() -> None:
        nonlocal stderr_truncated
        assert process.stderr is not None
        while chunk := process.stderr.read(8192):
            remaining = MAX_FFMPEG_STDERR_BYTES - len(retained)
            if remaining > 0:
                retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                stderr_truncated = True

    drain_thread = threading.Thread(
        target=drain_stderr,
        name="project-master-ffmpeg-stderr",
        daemon=True,
    )
    drain_thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        drain_thread.join()
        if process.stderr is not None:
            process.stderr.close()

    if timed_out:
        raise VideoTrimTimeoutError("FFmpeg exceeded the video trim time limit.")
    return VideoTrimProcessResult(
        returncode=process.returncode,
        stderr=bytes(retained),
        stderr_truncated=stderr_truncated,
    )


def _ffmpeg_seconds(value: float) -> str:
    rendered = f"{value:.9f}".rstrip("0").rstrip(".")
    return rendered or "0"


__all__ = [
    "MAX_FFMPEG_STDERR_BYTES",
    "VIDEO_TRIM_TIMEOUT_SECONDS",
    "VideoTrimBusyError",
    "VideoTrimError",
    "VideoTrimProcessError",
    "VideoTrimProcessResult",
    "VideoTrimTimeoutError",
    "VideoTrimUnavailableError",
    "VideoTrimValidationError",
    "build_video_trim_command",
    "run_bounded_process",
]
