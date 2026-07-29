from __future__ import annotations

from pathlib import Path

from project_master.media.models import MediaValidationError, normalize_media_type

_SIGNATURE_BYTES = 4096


def validate_media_signature(path: str | Path, media_type: str) -> None:
    """Reject bytes that do not match the declared supported media container."""
    normalized = normalize_media_type(media_type)
    with Path(path).open("rb") as media_file:
        header = media_file.read(_SIGNATURE_BYTES)
    validators = {
        "image/avif": _is_avif,
        "image/bmp": lambda value: value.startswith(b"BM"),
        "image/gif": lambda value: value.startswith((b"GIF87a", b"GIF89a")),
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/tiff": lambda value: value.startswith((b"II*\x00", b"MM\x00*")),
        "image/webp": _is_webp,
        "video/mp4": _is_iso_base_media,
        "video/mpeg": _is_mpeg_video,
        "video/quicktime": _is_iso_base_media,
        "video/webm": _is_ebml,
        "video/x-matroska": _is_ebml,
        "audio/aac": _is_aac,
        "audio/flac": lambda value: value.startswith(b"fLaC"),
        "audio/mp4": _is_iso_base_media,
        "audio/mpeg": _is_mpeg_audio,
        "audio/ogg": lambda value: value.startswith(b"OggS"),
        "audio/wav": _is_wave,
        "audio/webm": _is_ebml,
    }
    if not validators[normalized](header):
        raise MediaValidationError("File content does not match the declared supported media type.")


def _is_webp(value: bytes) -> bool:
    return len(value) >= 12 and value.startswith(b"RIFF") and value[8:12] == b"WEBP"


def _is_wave(value: bytes) -> bool:
    return len(value) >= 12 and value.startswith(b"RIFF") and value[8:12] == b"WAVE"


def _is_iso_base_media(value: bytes) -> bool:
    if len(value) < 12 or value[4:8] != b"ftyp":
        return False
    box_size = int.from_bytes(value[:4], "big")
    return box_size == 0 or 8 <= box_size <= len(value) or box_size >= 16


def _is_avif(value: bytes) -> bool:
    if not _is_iso_base_media(value):
        return False
    box_size = int.from_bytes(value[:4], "big")
    brands = value[8 : min(len(value), box_size if box_size >= 16 else 64)]
    return any(
        brands[index : index + 4] in {b"avif", b"avis"}
        for index in range(0, max(0, len(brands) - 3), 4)
    )


def _is_ebml(value: bytes) -> bool:
    return value.startswith(b"\x1a\x45\xdf\xa3")


def _is_mpeg_video(value: bytes) -> bool:
    return value.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3"))


def _is_mpeg_audio(value: bytes) -> bool:
    if value.startswith(b"ID3"):
        return True
    return len(value) >= 2 and value[0] == 0xFF and value[1] & 0xE0 == 0xE0


def _is_aac(value: bytes) -> bool:
    return len(value) >= 2 and value[0] == 0xFF and value[1] & 0xF6 == 0xF0
