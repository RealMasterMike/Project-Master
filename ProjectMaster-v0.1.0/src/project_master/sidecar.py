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


def main() -> None:
    configure_logging()
    from project_master.api import create_app

    logging.info("Starting Project Master desktop API on %s:%s", LOOPBACK_HOST, api_port())
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


if __name__ == "__main__":
    main()
