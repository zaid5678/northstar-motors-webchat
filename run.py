#!/usr/bin/env python3
"""Runs everything without Docker, on Windows, macOS, or Linux: python run.py

Needs Python 3.11 or later, and Node 20.19 or later the first time, to build the widget.
Add --check to start everything, confirm it answers, and stop.
"""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEBCHAT = ROOT / "webchat"
LOCAL = WEBCHAT / ".local"
ENVIRONMENT = LOCAL / "venv"
WIDGET = WEBCHAT / "src" / "northstar_webchat" / "static" / "widget.js"
PORTS = {4010: "platform", 4173: "website", 4020: "webchat"}
LOCAL_KEY = "northstar-local-development"  # The platform's published local key.


def stop(message):
    print("\n" + message)
    sys.exit(1)


def python_in_environment():
    folder = "Scripts" if os.name == "nt" else "bin"
    return str(ENVIRONMENT / folder / ("python.exe" if os.name == "nt" else "python"))


def prepare():
    if sys.version_info < (3, 11):
        stop("Python 3.11 or later is needed. This is {}.{}.".format(*sys.version_info[:2]))
    for port, name in PORTS.items():
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                stop("Port {} ({}) is already in use. Stop whatever is using it.".format(port, name))
    LOCAL.mkdir(exist_ok=True)
    if not (ENVIRONMENT / "installed").exists():
        print("Installing the webchat. This happens once.")
        subprocess.run([sys.executable, "-m", "venv", str(ENVIRONMENT)], check=True)
        subprocess.run(
            [python_in_environment(), "-m", "pip", "install", "--quiet", str(WEBCHAT)],
            check=True,
        )
        (ENVIRONMENT / "installed").touch()
    if not WIDGET.exists():
        npm = shutil.which("npm")
        if npm is None:
            stop("Node is needed once, to build the chat widget: https://nodejs.org")
        print("Building the chat widget. This happens once.")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=WEBCHAT / "widget", check=True)
        subprocess.run([npm, "run", "build"], cwd=WEBCHAT / "widget", check=True)
        subprocess.run(
            [python_in_environment(), "-m", "pip", "install", "--quiet", str(WEBCHAT)],
            check=True,
        )


def pepper():
    path = LOCAL / "pepper"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8")


def start():
    common = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    chat = dict(
        common,
        NORTHSTAR_API_KEY=LOCAL_KEY,
        WEBCHAT_PEPPER=pepper(),
        WEBCHAT_DATABASE_URL="sqlite:///" + (LOCAL / "webchat.sqlite3").as_posix(),
    )
    chat.setdefault("LLM_BASE_URL", "http://localhost:11434/v1")
    chat.setdefault("LLM_MODEL", "qwen3.5:9b")
    chat.setdefault("LLM_REASONING_EFFORT", "none")
    platform = dict(
        common,
        NORTHSTAR_DATA_PATH=str(LOCAL / "platform.sqlite3"),
        NORTHSTAR_API_KEY=LOCAL_KEY,
        NORTHSTAR_PORT="4010",
    )
    return [
        subprocess.Popen([sys.executable, "-m", "src.server"], cwd=ROOT / "dealership-platform", env=platform),
        subprocess.Popen([sys.executable, "server.py"], cwd=ROOT / "dealership-website", env=common),
        subprocess.Popen(
            [
                python_in_environment(), "-m", "uvicorn", "--factory",
                "northstar_webchat.main:create_from_env",
                "--host", "0.0.0.0", "--port", "4020", "--log-level", "warning",
            ],
            cwd=LOCAL,
            env=chat,
        ),
    ]


def answering(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def wait_until_ready(processes):
    urls = ("http://localhost:4010/health", "http://localhost:4173/", "http://localhost:4020/widget.js")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            return False
        if all(answering(url) for url in urls):
            return True
        time.sleep(0.5)
    return False


def main():
    prepare()
    processes = start()
    try:
        if not wait_until_ready(processes):
            stop("Something did not start. See the messages above.")
        if "--check" in sys.argv:
            print("All three services answered.")
            return
        print("\nOpen http://localhost:4173 and click the chat button. Press Ctrl+C to stop.")
        while all(process.poll() is None for process in processes):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
