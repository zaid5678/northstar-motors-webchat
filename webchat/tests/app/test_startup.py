"""The brief requires a clean checkout to run, with settings from the environment."""

from __future__ import annotations

import os
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


class TestPepperFile:
    """Docker has nowhere safe to keep a secret, so the server makes one on first start (D-032)."""

    def settings(self, tmp_path: Path) -> dict[str, str]:
        values = env(tmp_path, WEBCHAT_PEPPER_FILE=str(tmp_path / "secrets" / "pepper"))
        del values["WEBCHAT_PEPPER"]
        return values

    def test_it_is_made_on_first_start_and_kept(self, tmp_path: Path) -> None:
        create_from_env(self.settings(tmp_path))
        path = tmp_path / "secrets" / "pepper"
        first = path.read_text(encoding="utf-8")
        create_from_env(self.settings(tmp_path))
        assert path.read_text(encoding="utf-8") == first
        assert len(first) == 64

    def test_the_saved_pepper_is_the_one_used(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from northstar_webchat.writes.service import WriteService

        used: list[bytes] = []

        def capture(*args: object, pepper: bytes, **kwargs: object) -> object:
            used.append(pepper)
            return WriteService(*args, pepper=pepper, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("northstar_webchat.main.WriteService", capture)
        create_from_env(self.settings(tmp_path))
        assert used == [(tmp_path / "secrets" / "pepper").read_bytes()]

    def test_each_installation_gets_its_own(self, tmp_path: Path) -> None:
        create_from_env(self.settings(tmp_path / "a"))
        create_from_env(self.settings(tmp_path / "b"))
        assert (tmp_path / "a/secrets/pepper").read_text() != (tmp_path / "b/secrets/pepper").read_text()

    @pytest.mark.skipif(os.name == "nt", reason="Windows has no Unix file permissions")
    def test_only_the_owner_can_read_it(self, tmp_path: Path) -> None:
        create_from_env(self.settings(tmp_path))
        assert (tmp_path / "secrets" / "pepper").stat().st_mode & 0o077 == 0

    def test_a_set_pepper_is_used_instead(self, tmp_path: Path) -> None:
        values = env(tmp_path, WEBCHAT_PEPPER_FILE=str(tmp_path / "pepper"))
        create_from_env(values)
        assert not (tmp_path / "pepper").exists()

    def test_an_empty_file_is_an_error_not_a_blank_pepper(self, tmp_path: Path) -> None:
        values = self.settings(tmp_path)
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "pepper").write_text("", encoding="utf-8")
        with pytest.raises(SettingsError):
            create_from_env(values)

    def test_compose_commits_no_pepper_value(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        assert "local-development-only" not in compose
        assert "WEBCHAT_PEPPER_FILE" in compose


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


def test_startup_logs_which_model_was_chosen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from northstar_webchat import ops

    # Nothing listens on port 9, so the first choice is not there.
    values = env(
        tmp_path,
        LLM_BASE_URL="http://127.0.0.1:9/v1",
        LLM_MODEL="big",
        LLM_FALLBACK_BASE_URL="http://bundled.test/v1",
        LLM_FALLBACK_MODEL="small",
    )
    try:
        create_from_env(values)
    finally:
        ops.LOGGER.handlers.clear()
        ops.LOGGER.propagate = True
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    [startup] = [line for line in lines if line["event"] == "startup"]
    assert (startup["model"], startup["llm_choice"]) == ("small", "fallback")


def test_startup_is_logged_without_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from northstar_webchat import ops

    try:
        create_from_env(
            env(
                tmp_path,
                LLM_BASE_URL="http://127.0.0.1:9/v1",
                LLM_API_KEY="llm-secret-value",
                LLM_MODEL="qwen3.5:9b",
            )
        )
    finally:
        ops.LOGGER.handlers.clear()
        ops.LOGGER.propagate = True
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    [startup] = [line for line in lines if line["event"] == "startup"]
    assert startup["model"] == "qwen3.5:9b"
    everything = json.dumps(lines)
    for secret in ("llm-secret-value", "pepper", "chat.sqlite3"):
        assert secret not in everything

