from __future__ import annotations

import ipaddress
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_DNS_NAME = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_LOOPBACK_HOSTS = frozenset({"localhost", "localhost.localdomain"})
_FORBIDDEN_AUTH_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "proxy-authorization",
        "transfer-encoding",
    }
)


class ComfySecurityError(ValueError):
    """Raised when an integration value crosses a configured trust boundary."""


def normalize_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    if not host or "/" in host or "\\" in host or "@" in host:
        raise ComfySecurityError("Trusted hosts must be bare host names or IP addresses.")
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ComfySecurityError("Trusted host is not a valid host name.") from exc
        if not _DNS_NAME.fullmatch(ascii_host):
            raise ComfySecurityError("Trusted host is not a valid host name.") from None
        return ascii_host


def is_loopback_host(value: str) -> bool:
    host = normalize_host(value)
    if host in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_base_url(value: str, trusted_hosts: tuple[str, ...] = ()) -> str:
    """Validate and normalize a ComfyUI root URL.

    Loopback is trusted by default. Every remote host must be named explicitly and must use
    HTTPS. DNS is intentionally not resolved here: the HTTP layer must connect only to the
    already validated URL and must not follow redirects.
    """

    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ComfySecurityError("ComfyUI URLs must use http:// or https://.")
    if not parsed.hostname:
        raise ComfySecurityError("ComfyUI URL must include a host.")
    if parsed.username is not None or parsed.password is not None:
        raise ComfySecurityError("Credentials must use a secret reference, not URL user info.")
    if parsed.query or parsed.fragment:
        raise ComfySecurityError("ComfyUI base URL cannot include a query or fragment.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ComfySecurityError("ComfyUI URL has an invalid port.") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ComfySecurityError("ComfyUI URL has an invalid port.")

    host = normalize_host(parsed.hostname)
    trusted = {normalize_host(item) for item in trusted_hosts}
    local = is_loopback_host(host)
    if not local and host not in trusted:
        raise ComfySecurityError("Remote ComfyUI hosts must be explicitly listed in trusted_hosts.")
    if not local and parsed.scheme != "https":
        raise ComfySecurityError("Remote ComfyUI connections must use HTTPS.")

    path = _normalize_base_path(parsed.path)
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if port is None else f"{display_host}:{port}"
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def join_api_url(base_url: str, route: str) -> str:
    parsed_route = urlsplit(route)
    if parsed_route.scheme or parsed_route.netloc or parsed_route.query or parsed_route.fragment:
        raise ComfySecurityError("ComfyUI API routes must be relative paths without a query.")
    if "\\" in route or "\x00" in route:
        raise ComfySecurityError("ComfyUI API route contains an unsafe path character.")
    route_parts = [unquote(part) for part in parsed_route.path.split("/") if part]
    if not route_parts or any(not _safe_path_component(part) for part in route_parts):
        raise ComfySecurityError("ComfyUI API route is invalid.")

    parsed_base = urlsplit(base_url)
    base_path = parsed_base.path.rstrip("/")
    path = f"{base_path}/{'/'.join(route_parts)}"
    return urlunsplit((parsed_base.scheme, parsed_base.netloc, path, "", ""))


def validate_identifier(value: str, label: str = "identifier") -> str:
    if not _SAFE_ID.fullmatch(value):
        raise ComfySecurityError(f"ComfyUI {label} contains unsupported characters.")
    return value


def validate_auth_header(value: str) -> str:
    if not _SAFE_HEADER.fullmatch(value):
        raise ComfySecurityError("Authentication header name is invalid.")
    if value.lower() in _FORBIDDEN_AUTH_HEADERS:
        raise ComfySecurityError("Authentication header is not permitted.")
    return value


def validate_output_locator(filename: str, subfolder: str, output_type: str) -> None:
    """Validate untrusted output metadata returned by ComfyUI.

    `/view` accepts filename and subfolder separately. Keeping filename to one component and
    subfolder relative prevents a server response from being treated as a local filesystem path.
    """

    if output_type not in {"output", "temp"}:
        raise ComfySecurityError("Only ComfyUI output and temp artifacts may be referenced.")
    _validate_file_locator(filename, subfolder, label="output")


def validate_input_locator(filename: str, subfolder: str, input_type: str) -> None:
    """Validate one untrusted input locator returned by ComfyUI after an upload."""

    if input_type != "input":
        raise ComfySecurityError("Only ComfyUI input artifacts may be referenced.")
    _validate_file_locator(filename, subfolder, label="input")


def _validate_file_locator(filename: str, subfolder: str, *, label: str) -> None:
    if (
        not filename
        or not filename.strip()
        or len(filename) > 240
        or len(filename.encode("utf-8")) > 255
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
    ):
        raise ComfySecurityError(f"ComfyUI {label} filename must be a single safe path component.")
    if (
        len(subfolder) > 1_024
        or "\x00" in subfolder
        or "\\" in subfolder
        or any(ord(character) < 32 or ord(character) == 127 for character in subfolder)
    ):
        raise ComfySecurityError(f"ComfyUI {label} subfolder is invalid.")
    folder = PurePosixPath(subfolder)
    if folder.is_absolute() or any(
        part in {".", ".."} or len(part.encode("utf-8")) > 255 for part in folder.parts
    ):
        raise ComfySecurityError(f"ComfyUI {label} subfolder must remain relative.")


def _normalize_base_path(path: str) -> str:
    if "\\" in path or "\x00" in path:
        raise ComfySecurityError("ComfyUI URL path contains an unsafe character.")
    parts = [unquote(part) for part in path.split("/") if part]
    if any(not _safe_path_component(part) for part in parts):
        raise ComfySecurityError("ComfyUI URL path cannot traverse directories.")
    return f"/{'/'.join(parts)}" if parts else ""


def _safe_path_component(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )
