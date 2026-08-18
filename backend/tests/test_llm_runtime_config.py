from pathlib import Path

import pytest

from app.config import (
    build_llm_env_updates,
    clear_settings_cache,
    get_settings,
    reload_llm_from_env_file,
    set_env_file_path_for_tests,
    update_env_file,
)
from app.services import llm_service, model_registry


@pytest.fixture
def llm_env_file(tmp_path: Path):
    env_path = tmp_path / ".env"
    set_env_file_path_for_tests(env_path)
    clear_settings_cache()
    llm_service.reset_llm_service()
    yield env_path
    set_env_file_path_for_tests(None)
    clear_settings_cache()
    llm_service.reset_llm_service()


def test_reload_overrides_stale_process_environment(llm_env_file: Path, monkeypatch):
    """Docker-injected env must lose to the bind-mounted .env after save."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "old-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("QWEN_API_KEY", "")
    monkeypatch.setenv("QWEN_MODEL", "stale-qwen")
    llm_env_file.write_text(
        "LLM_PROVIDER=qwen\n"
        "QWEN_API_KEY=new-qwen-key\n"
        "QWEN_MODEL=qwen3.7-plus\n",
        encoding="utf-8",
    )

    assert reload_llm_from_env_file(force=True) is True

    settings = get_settings()
    assert settings.llm_provider == "qwen"
    assert settings.qwen_api_key == "new-qwen-key"
    assert settings.qwen_model == "qwen3.7-plus"
    assert llm_service.get_llm_service().get_active_provider_name() == "qwen"
    assert llm_service.get_llm_service().supports_vision() is True


def test_reload_skips_when_env_mtime_unchanged(llm_env_file: Path):
    llm_env_file.write_text("LLM_PROVIDER=qwen\nQWEN_API_KEY=key\n", encoding="utf-8")
    assert reload_llm_from_env_file(force=True) is True
    assert reload_llm_from_env_file() is False


def test_update_env_file_then_reload_switches_model(llm_env_file: Path, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "key")
    monkeypatch.setenv("QWEN_MODEL", "qwen-turbo")
    llm_env_file.write_text(
        "LLM_PROVIDER=qwen\nQWEN_API_KEY=key\nQWEN_MODEL=qwen-turbo\n",
        encoding="utf-8",
    )
    reload_llm_from_env_file(force=True)

    update_env_file({"QWEN_MODEL": "qwen3-vl-plus"})
    assert reload_llm_from_env_file() is True
    assert get_settings().qwen_model == "qwen3-vl-plus"
    assert llm_service.get_llm_service().supports_vision() is True


def test_build_llm_env_updates_keeps_existing_key_when_blank():
    updates = build_llm_env_updates(
        "qwen",
        api_key="  ",
        model="qwen3.7-plus",
        base_url=None,
        existing_api_key="saved-key",
    )
    assert updates["LLM_PROVIDER"] == "qwen"
    assert updates["QWEN_MODEL"] == "qwen3.7-plus"
    assert "QWEN_API_KEY" not in updates


def test_build_llm_env_updates_requires_key_for_new_provider():
    with pytest.raises(ValueError, match="API密钥不能为空"):
        build_llm_env_updates(
            "openai",
            api_key=None,
            model="gpt-4o",
            base_url=None,
            existing_api_key="",
        )


def test_openai_and_deepseek_registry_ids_are_native():
    openai_models = model_registry.get_models_with_fallback("openai")
    deepseek_models = model_registry.get_models_with_fallback("deepseek")
    assert openai_models[0]["id"] == "gpt-4o"
    assert all("/" not in model["id"] for model in openai_models)
    assert all("/" not in model["id"] for model in deepseek_models)
    assert all(model["vision"] is False for model in deepseek_models)
