from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAX_FFPROBE_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None


def probe_media_metadata(
    path: Path,
    *,
    executable: str = "ffprobe",
    timeout_seconds: float = 5.0,
) -> MediaMetadata:
    """Return bounded best-effort metadata without making ffprobe a dependency."""
    command = (
        executable,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "format=duration:stream=duration,width,height",
        "-of",
        "json",
        str(path),
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return MediaMetadata()
    if result.returncode != 0 or len(result.stdout) > _MAX_FFPROBE_OUTPUT_BYTES:
        return MediaMetadata()
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return MediaMetadata()
    if not isinstance(payload, dict):
        return MediaMetadata()

    streams = payload.get("streams")
    stream = streams[0] if isinstance(streams, list) and streams else {}
    if not isinstance(stream, dict):
        stream = {}
    media_format = payload.get("format")
    if not isinstance(media_format, dict):
        media_format = {}
    duration = _finite_nonnegative(stream.get("duration"))
    if duration is None:
        duration = _finite_nonnegative(media_format.get("duration"))
    return MediaMetadata(
        duration_seconds=duration,
        width=_positive_integer(stream.get("width")),
        height=_positive_integer(stream.get("height")),
    )


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
