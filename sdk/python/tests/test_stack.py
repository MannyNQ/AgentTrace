"""`trayce up` / `trayce down` without Docker, and the bundled stack against the repo's compose."""

import socket
import subprocess
from pathlib import Path

import pytest
import yaml

from trayce import cli, server

REPO = Path(__file__).resolve().parents[3]
STACK = Path(server.__file__).parent / "stack"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_the_bundled_stack_matches_the_repo_compose():
    dev, bundled = load(REPO / "docker-compose.yml"), load(STACK / "compose.yml")
    assert set(bundled["services"]) == set(dev["services"])
    for name in ("postgres", "clickhouse", "redis", "seaweedfs"):
        assert bundled["services"][name]["image"] == dev["services"][name]["image"], name
    for name in ("ingest", "worker", "web"):
        assert bundled["services"][name]["environment"].keys() == (
            dev["services"][name]["environment"].keys()
        ), name
        assert bundled["services"][name].get("command") == dev["services"][name].get("command")
        assert bundled["services"][name]["depends_on"] == dev["services"][name]["depends_on"]


def test_the_wheel_ships_the_stack():
    assert {p.name for p in STACK.iterdir()} >= set(server.STACK_FILES)


@pytest.fixture
def fake_docker(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setenv("TRAYCE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server, "port_free", lambda host, port: True)
    return calls


def main(*argv: str) -> int:
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return int(e.value.code or 0)


def test_up_writes_secrets_once_and_prints_how_to_connect(fake_docker, tmp_path, capsys):
    assert main("up") == 0
    root = tmp_path / "home"
    env = server.read_env(root / ".env")
    assert env["TRAYCE_API_KEY"].startswith("tr-") and env["TRAYCE_SETUP_TOKEN"]
    assert env["POSTGRES_PASSWORD"] != "trayce"
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    assert (root / "compose.yml").read_text() == (STACK / "compose.yml").read_text()
    up = fake_docker[-1]
    assert up[:4] == ["docker", "compose", "-p", "trayce-local"]
    assert up[-5:] == ["up", "-d", "--wait", "--wait-timeout", "300"]
    out = capsys.readouterr().out
    assert f"TRAYCE_API_KEY={env['TRAYCE_API_KEY']}" in out
    assert f"/setup with token {env['TRAYCE_SETUP_TOKEN']}" in out
    assert "OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000/otlp" in out

    # A second run keeps the secrets, and edits to .env, but refreshes the stack files.
    (root / ".env").write_text((root / ".env").read_text().replace("=3000", "=3100"))
    (root / "compose.yml").write_text("stale")
    assert main("up") == 0
    assert server.read_env(root / ".env")["TRAYCE_API_KEY"] == env["TRAYCE_API_KEY"]
    assert (root / "compose.yml").read_text() == (STACK / "compose.yml").read_text()
    assert "Web UI   http://localhost:3100" in capsys.readouterr().out


def test_up_tags_images_with_the_package_version(fake_docker, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda cmd, **kw: (
            seen.update(kw.get("env") or {}) or subprocess.CompletedProcess(cmd, 0, b"", b"")
        ),
    )
    monkeypatch.delenv("TRAYCE_VERSION", raising=False)
    assert main("up") == 0
    assert seen["TRAYCE_VERSION"] == server.metadata.version("trayce")
    monkeypatch.setenv("TRAYCE_VERSION", "9.9.9")
    assert main("up") == 0
    assert seen["TRAYCE_VERSION"] == "9.9.9"


def test_up_names_the_setting_for_a_port_in_use(fake_docker, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(server, "port_free", lambda host, port: port != 8000)
    assert main("up") == 2
    err = capsys.readouterr().err
    assert "8000 (TRAYCE_INGEST_PORT)" in err and "3000" not in err
    assert str(tmp_path / "home" / ".env") in err
    assert not any("up" in cmd for cmd in fake_docker)


def test_up_skips_the_port_check_when_its_own_stack_is_running(fake_docker, monkeypatch):
    calls = fake_docker

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, b"abc123\n" if "ps" in cmd else b"", b"")

    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server, "port_free", lambda host, port: False)
    assert main("up") == 0
    assert calls[-1][-5:] == ["up", "-d", "--wait", "--wait-timeout", "300"]


def test_port_free_sees_a_listening_socket():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert not server.port_free("127.0.0.1", s.getsockname()[1])


def test_down_keeps_data_unless_asked(fake_docker):
    assert main("up") == 0
    assert main("down") == 0
    assert fake_docker[-1][-1] == "down"
    assert main("down", "--delete-data") == 0
    assert fake_docker[-1][-2:] == ["down", "--volumes"]


def test_without_docker_up_says_what_to_install(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TRAYCE_HOME", str(tmp_path))
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    assert main("up") == 2
    assert "needs Docker" in capsys.readouterr().err
    assert not (tmp_path / ".env").exists()
