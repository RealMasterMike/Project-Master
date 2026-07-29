"""Packaged loopback API entry point owned by the Tauri desktop process."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8765


def api_port() -> int:
    raw = os.getenv("MASTER_API_PORT", str(DEFAULT_API_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("MASTER_API_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("MASTER_API_PORT must be between 1 and 65535")
    return port


def configure_logging() -> Path | None:
    raw_path = os.getenv("MASTER_LOG_PATH")
    if not raw_path:
        logging.basicConfig(level=logging.INFO)
        return None

    log_path = Path(raw_path).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    return log_path


def pid_file_path() -> Path | None:
    raw_path = os.getenv("MASTER_PID_PATH")
    if not raw_path:
        return None
    return Path(raw_path).expanduser().resolve()


def write_pid_file() -> Path | None:
    """Record this process id so the desktop app can reclaim a stale port.

    A backend orphaned by a hard kill keeps holding the loopback port, and the
    next launch has no handle to it. The pid file gives the launcher something
    to terminate instead of refusing to start.
    """
    path = pid_file_path()
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        logging.warning("Unable to write the backend pid file at %s", path)
        return None
    return path


def remove_pid_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logging.warning("Unable to remove the backend pid file at %s", path)


def main() -> None:
    configure_logging()
    from project_master.api import create_app

    logging.info("Starting Project Master desktop API on %s:%s", LOOPBACK_HOST, api_port())
    pid_path = write_pid_file()
    try:
        uvicorn.run(
            create_app(),
            host=LOOPBACK_HOST,
            port=api_port(),
            log_config=None,
            access_log=False,
        )
    except Exception:
        logging.exception("Project Master desktop API stopped unexpectedly")
        raise
    finally:
        remove_pid_file(pid_path)


if __name__ == "__main__":
    main()
