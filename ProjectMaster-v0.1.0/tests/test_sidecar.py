from pathlib import Path

import pytest

from project_master.sidecar import DEFAULT_API_PORT, LOOPBACK_HOST, api_port, configure_logging


def test_sidecar_is_fixed_to_loopback() -> None:
    assert LOOPBACK_HOST == "127.0.0.1"


def test_sidecar_port_defaults_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASTER_API_PORT", raising=False)
    assert api_port() == DEFAULT_API_PORT

    monkeypatch.setenv("MASTER_API_PORT", "49152")
    assert api_port() == 49152

    for invalid in ("not-a-port", "0", "65536"):
        monkeypatch.setenv("MASTER_API_PORT", invalid)
        with pytest.raises(ValueError):
            api_port()


def test_sidecar_log_path_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = tmp_path / "logs" / "backend.log"
    monkeypatch.setenv("MASTER_LOG_PATH", str(expected))

    assert configure_logging() == expected.resolve()
    assert expected.parent.is_dir()
