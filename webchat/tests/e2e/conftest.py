"""Real browser, real website server, real platform, real webchat. Needs ports 4010, 4173, 4020."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[3]
WEBCHAT = ROOT / "webchat"
SITE = "http://localhost:4173"


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_for(url: str) -> None:
    deadline = time.monotonic() + 20
    while True:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise RuntimeError(f"{url} did not start")
            time.sleep(0.1)


# Package scope: Playwright's sync API keeps an event loop running while open, which would block
# every async test that runs after these.
@pytest.fixture(scope="package")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    busy = [port for port in (4010, 4173, 4020) if port_in_use(port)]
    if busy:
        pytest.skip(f"ports in use: {busy}")
    data = tmp_path_factory.mktemp("e2e")
    base = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    processes = [
        subprocess.Popen(
            [sys.executable, "-m", "src.server"],
            cwd=ROOT / "dealership-platform",
            env={**base, "NORTHSTAR_DATA_PATH": str(data / "p.sqlite3"), "NORTHSTAR_PORT": "4010"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
        subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=ROOT / "dealership-website",
            env=base,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ),
        subprocess.Popen(
            [sys.executable, "-m", "tests.e2e.server"],
            cwd=WEBCHAT,
            env={
                **base,
                "WEBCHAT_DATABASE_URL": f"sqlite:///{data}/chat.sqlite3",
                "NORTHSTAR_PLATFORM_URL": "http://localhost:4010",
            },
        ),
    ]
    try:
        for url in ("http://localhost:4010/health", SITE, "http://localhost:4020/health"):
            wait_for(url)
        yield
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            process.wait(timeout=5)


@pytest.fixture(scope="package")
def browser(stack: None) -> Iterator[Browser]:
    with sync_playwright() as driver:
        instance = driver.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    instance = context.new_page()
    errors: list[str] = []
    instance.on("pageerror", lambda error: errors.append(str(error)))
    instance.goto(SITE)
    yield instance
    context.close()
    assert not errors, errors


@pytest.fixture
def phone(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 390, "height": 740}, has_touch=True)
    instance = context.new_page()
    instance.goto(SITE)
    yield instance
    context.close()
