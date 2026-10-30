"""`trayce up` and `trayce down`: run a Trayce server on this machine from published images
(ADR-0043). Only Docker is needed: the compose file ships in this package (`stack/`), and the
images are tagged with this package's version.

Everything lives in TRAYCE_HOME (default ~/.trayce): the compose file and SeaweedFS start script,
copied fresh on each `up` so an upgraded package runs its own stack, and a `.env` written once
with random secrets, which later runs keep. Exit codes: 0 on success, 2 when Docker is missing or
a port the stack publishes is taken, and Docker's own code when compose fails.
"""

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
from importlib import metadata, resources
from pathlib import Path

STACK_FILES = ("compose.yml", "seaweedfs-start.sh")
PROJECT = "trayce-local"


def home() -> Path:
    return Path(os.environ.get("TRAYCE_HOME") or Path.home() / ".trayce")


def new_env() -> dict[str, str]:
    """A first run's settings. The setup token is set rather than left to ingest's logs, so `up`
    can print it (ADR-0034)."""
    return {
        "TRAYCE_API_KEY": "tr-" + secrets.token_urlsafe(32),
        "TRAYCE_SETUP_TOKEN": secrets.token_urlsafe(24),
        "POSTGRES_USER": "trayce",
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "POSTGRES_DB": "trayce",
        "CLICKHOUSE_USER": "trayce",
        "CLICKHOUSE_PASSWORD": secrets.token_hex(16),
        "S3_ACCESS_KEY": "trayce",
        "S3_SECRET_KEY": secrets.token_hex(16),
        "S3_BUCKET": "payloads",
        "TRAYCE_INGEST_PORT": "8000",
        "TRAYCE_WEB_PORT": "3000",
    }


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            env[key.strip()] = value.strip()
    return env


def prepare(root: Path) -> dict[str, str]:
    """Copies the stack into `root` and returns its settings, writing `.env` on the first run."""
    root.mkdir(parents=True, exist_ok=True)
    stack = resources.files("trayce") / "stack"
    for name in STACK_FILES:
        (root / name).write_text(stack.joinpath(name).read_text())
    env_path = root / ".env"
    if not env_path.exists():
        lines = ["# Written by `trayce up`. Edit and run it again to apply."]
        lines += [f"{k}={v}" for k, v in new_env().items()]
        env_path.write_text("\n".join(lines) + "\n")
        env_path.chmod(0o600)
    return read_env(env_path)


def version() -> str:
    """The image tag: this package's version, unless TRAYCE_VERSION names another."""
    return os.environ.get("TRAYCE_VERSION") or metadata.version("trayce")


def compose_cmd(root: Path, *args: str) -> list[str]:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(root / "compose.yml")]
    return cmd + ["--env-file", str(root / ".env"), *args]


def compose(root: Path, *args: str) -> int:
    env = {**os.environ, "TRAYCE_VERSION": version()}
    return subprocess.run(compose_cmd(root, *args), env=env).returncode


def port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def taken_ports(root: Path, env: dict[str, str]) -> list[str]:
    """The published ports something else holds, as "port (SETTING)". Docker's own error for this
    names neither the port's setting nor the file to change it in. A running stack of our own
    holds its ports, so it skips the check and compose applies any change."""
    ps = subprocess.run(compose_cmd(root, "ps", "-q"), capture_output=True)
    if ps.returncode == 0 and ps.stdout.strip():
        return []

    def get(key: str, default: str) -> str:
        # Compose takes these from the shell first, then .env, as it does for every variable.
        return os.environ.get(key) or env.get(key) or default

    ingest_host = get("TRAYCE_INGEST_BIND", "127.0.0.1")
    published = [
        ("TRAYCE_INGEST_PORT", ingest_host, get("TRAYCE_INGEST_PORT", "8000")),
        ("TRAYCE_WEB_PORT", "127.0.0.1", get("TRAYCE_WEB_PORT", "3000")),
    ]
    return [f"{port} ({key})" for key, host, port in published if not port_free(host, int(port))]


def docker_missing() -> str | None:
    if shutil.which("docker") is None:
        return "trayce up needs Docker: https://docs.docker.com/get-docker/"
    try:
        out = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        out = None
    if out is None or out.returncode != 0:
        return "trayce up needs Docker Compose v2 (`docker compose`), and Docker running"
    return None


def banner(env: dict[str, str]) -> str:
    web = f"http://localhost:{env.get('TRAYCE_WEB_PORT', '3000')}"
    ingest = f"http://localhost:{env.get('TRAYCE_INGEST_PORT', '8000')}"
    key = env["TRAYCE_API_KEY"]
    lines = [
        "",
        f"Trayce {version()} is running.",
        "",
        f"  Web UI   {web}",
        f"  Ingest   {ingest}",
    ]
    if token := env.get("TRAYCE_SETUP_TOKEN"):
        lines.append(f"  First visit? Create the owner account at {web}/setup with token {token}")
    lines += [
        "",
        "Point the trayce SDK or CLI at it:",
        f"  TRAYCE_ENDPOINT={ingest}",
        f"  TRAYCE_API_KEY={key}",
        "",
        "Or any OpenTelemetry exporter (OTLP/HTTP):",
        f"  OTEL_EXPORTER_OTLP_ENDPOINT={ingest}/otlp",
        f"  OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20{key}",
        "  OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT=true",
        "",
        "`trayce down` stops it; your data stays until `trayce down --delete-data`.",
    ]
    return "\n".join(lines)


def run_up(args: argparse.Namespace) -> int:
    if problem := docker_missing():
        print(problem, file=sys.stderr)
        return 2
    root = home()
    env = prepare(root)
    if taken := taken_ports(root, env):
        print(
            f"trayce up: in use by something else: {', '.join(taken)}.\n"
            f"Set free ports in {root / '.env'} and run `trayce up` again.",
            file=sys.stderr,
        )
        return 2
    print(f"Starting Trayce {version()} from {root} (the first run pulls the images)...")
    code = compose(root, "up", "-d", "--wait", "--wait-timeout", str(args.wait_timeout))
    if code != 0:
        print(
            f"trayce up: docker compose failed. See `docker compose -p {PROJECT} logs`.",
            file=sys.stderr,
        )
        return code
    print(banner(env))
    return 0


def run_down(args: argparse.Namespace) -> int:
    if problem := docker_missing():
        print(problem, file=sys.stderr)
        return 2
    root = home()
    if not (root / "compose.yml").exists():
        print(f"trayce down: nothing to stop; {root} has no stack", file=sys.stderr)
        return 0
    return compose(root, "down", *(["--volumes"] if args.delete_data else []))


def add_parsers(sub) -> None:
    up = sub.add_parser("up", help="start a Trayce server on this machine (needs Docker)")
    up.add_argument(
        "--wait-timeout",
        type=int,
        default=300,
        help="seconds to wait for every service to be healthy (default: 300)",
    )
    down = sub.add_parser("down", help="stop the server `trayce up` started")
    down.add_argument(
        "--delete-data",
        action="store_true",
        help="also delete its traces, datasets, and accounts",
    )
