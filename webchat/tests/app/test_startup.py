"""The brief requires a clean checkout to run, with settings from the environment."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from northstar_webchat.main import SettingsError, create_from_env

ROOT = Path(__file__).resolve().parents[3]


def env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    return {
        "NORTHSTAR_API_KEY": "key",
        "WEBCHAT_PEPPER": "pepper",
        "WEBCHAT_DATABASE_URL": f"sqlite:///{tmp_path}/chat.sqlite3",
        **overrides,
    }


@pytest.mark.parametrize("missing", ["NORTHSTAR_API_KEY", "WEBCHAT_PEPPER"])
def test_a_missing_secret_is_a_clear_error(tmp_path: Path, missing: str) -> None:
    values = env(tmp_path)
    del values[missing]
    with pytest.raises(SettingsError) as caught:
        create_from_env(values)
    assert missing in str(caught.value)


async def test_it_starts_with_no_llm_configured(tmp_path: Path) -> None:
    app = create_from_env(env(tmp_path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://chat.test"
    ) as browser:
        assert (await browser.get("/health")).json() == {"status": "ok"}


def test_the_database_is_migrated_on_start(tmp_path: Path) -> None:
    create_from_env(env(tmp_path))
    assert (tmp_path / "chat.sqlite3").exists()


def test_required_environment_names_are_documented_without_values() -> None:
    example = (ROOT / "webchat" / ".env.example").read_text(encoding="utf-8")
    for name in ["NORTHSTAR_API_KEY", "WEBCHAT_PEPPER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"]:
        assert name in example
    assert "northstar-local-development" not in example


def test_the_website_does_not_contain_the_api_key() -> None:
    website = ROOT / "dealership-website"
    for path in website.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".html", ".css"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "northstar-local-development" not in text
            assert "X-API-Key" not in text


def test_no_webchat_source_contains_a_secret_value() -> None:
    for path in (ROOT / "webchat" / "src").rglob("*.py"):
        assert "northstar-local-development" not in path.read_text(encoding="utf-8")


def test_startup_is_logged_without_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from northstar_webchat import ops

    try:
        create_from_env(env(tmp_path, LLM_API_KEY="llm-secret-value", LLM_MODEL="qwen3.5:9b"))
    finally:
        ops.LOGGER.handlers.clear()
        ops.LOGGER.propagate = True
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    [startup] = [line for line in lines if line["event"] == "startup"]
    assert startup["model"] == "qwen3.5:9b"
    everything = json.dumps(lines)
    for secret in ("llm-secret-value", "pepper", "chat.sqlite3"):
        assert secret not in everything

