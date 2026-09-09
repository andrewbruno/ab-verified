"""Run the real application against a throwaway database.

Shared by the end-to-end suite and by scripts/record_demo.py, so that what is
recorded for the walkthrough is the same application, started the same way, as
what the tests assert against.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def running_app(db_path: Path, port: int | None = None) -> Iterator[str]:
    """Yield the base URL of a freshly seeded application.

    APP_ENV is `development` rather than `test` deliberately. Every other
    environment marks the session cookie Secure (NFR-04), and a Secure cookie
    is not returned over the plain HTTP spoken to localhost, so every sign-in
    would appear to succeed and then be forgotten.
    """
    port = port or free_port()
    env = {
        **os.environ,
        "APP_ENV": "development",
        "DEMO_MODE": "true",
        "SQLITE_PATH": str(db_path),
        "SESSION_SECRET": "e2e-only-secret",
        "CRON_SECRET": "e2e-only-secret",
        "DATABASE_URL": "",
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"the server exited early:\n{out}")
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("the server did not become ready within 45 seconds")

    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
