"""Docker runs the AI too, so a reviewer needs nothing but Docker (D-033)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
MODEL = "qwen2.5:3b"

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs the docker command")


def config(**env: str) -> dict[str, Any]:
    """What Compose will run, with variables filled in. Needs no running Docker engine."""
    names = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "WEBCHAT_PEPPER")
    clean = {k: v for k, v in os.environ.items() if k not in names}
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=ROOT,
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=True,
    )
    services: dict[str, Any] = json.loads(result.stdout)["services"]
    return services


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    return config()


def test_ollama_on_the_computer_is_the_first_choice(services: dict[str, Any]) -> None:
    chat = services["webchat"]
    assert chat["environment"]["LLM_BASE_URL"] == "http://host.docker.internal:11434/v1"
    assert chat["environment"]["LLM_MODEL"] == "qwen3.5:9b"
    hosts = [entry.replace("=", ":") for entry in chat["extra_hosts"]]
    assert "host.docker.internal:host-gateway" in hosts


def test_the_bundled_model_is_the_fallback(services: dict[str, Any]) -> None:
    chat = services["webchat"]["environment"]
    assert chat["LLM_FALLBACK_BASE_URL"] == "http://ollama:11434/v1"
    assert chat["LLM_FALLBACK_MODEL"] == MODEL
    assert float(chat["LLM_FALLBACK_TIMEOUT_SECONDS"]) >= 60


def test_the_bundled_model_is_the_one_downloaded(services: dict[str, Any]) -> None:
    assert MODEL in " ".join(services["ollama-model"]["command"])


def test_the_first_choice_can_be_changed() -> None:
    chat = config(LLM_BASE_URL="http://other.test/v1", LLM_MODEL="other")["webchat"]
    assert chat["environment"]["LLM_BASE_URL"] == "http://other.test/v1"
    assert chat["environment"]["LLM_MODEL"] == "other"


def test_the_model_server_opens_no_port_on_the_computer(services: dict[str, Any]) -> None:
    """So it cannot clash with Ollama already on the host, or be reached from the network."""
    assert not services["ollama"].get("ports")


def test_the_image_version_is_pinned(services: dict[str, Any]) -> None:
    for name in ("ollama", "ollama-model"):
        image = services[name]["image"]
        assert ":" in image and not image.endswith(":latest")
    assert services["ollama"]["image"] == services["ollama-model"]["image"]


def test_the_model_is_kept_between_starts(services: dict[str, Any]) -> None:
    mounts = services["ollama"]["volumes"]
    assert any(m["target"] == "/root/.ollama" and m["type"] == "volume" for m in mounts)


def test_the_model_stays_loaded(services: dict[str, Any]) -> None:
    """Ollama unloads after five idle minutes, and reloading costs a customer most of a minute."""
    assert services["ollama"]["environment"]["OLLAMA_KEEP_ALIVE"] == "-1"


def test_the_chat_waits_for_the_download(services: dict[str, Any]) -> None:
    needs = services["webchat"]["depends_on"]
    assert needs["ollama-model"]["condition"] == "service_completed_successfully"


def test_a_failed_download_still_lets_the_chat_start(services: dict[str, Any]) -> None:
    """Offline, the download fails. The chat must still start, in its reduced mode."""
    pull = services["ollama-model"]
    script = " ".join(pull["command"])
    assert pull["entrypoint"][:2] == ["/bin/sh", "-c"]
    assert "||" in script
    # The script runs in a Linux container. Windows test machines may have no shell to try it in.
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("no sh to run the script in")
    completed = subprocess.run([shell, "-c", script.replace("ollama pull", "false")])
    assert completed.returncode == 0
