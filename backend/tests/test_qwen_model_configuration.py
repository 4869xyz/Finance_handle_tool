from app.config import Settings
from app.services import llm_service, model_registry


def test_qwen_3_7_plus_is_the_default_model(monkeypatch):
    """New installations must use the requested Qwen model without extra setup."""
    monkeypatch.delenv("QWEN_MODEL", raising=False)

    assert Settings(_env_file=None).qwen_model == "qwen3.7-plus"


def test_qwen_registry_returns_dashscope_native_model_ids(monkeypatch):
    """Every Qwen option shown in settings must be directly callable by DashScope."""
    monkeypatch.setattr(
        model_registry,
        "get_formatted_models_for_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Qwen must not use OpenRouter model IDs")
        ),
    )

    models = model_registry.get_models_with_fallback("qwen")

    assert models[0] == {
        "id": "qwen3.7-plus",
        "name": "Qwen 3.7 Plus",
        "vision": True,
    }
    assert all("/" not in model["id"] for model in models)


def test_qwen_3_7_plus_uses_the_vision_request_path(monkeypatch):
    """Invoice images must not be downgraded to text-only processing."""
    monkeypatch.setattr(llm_service.settings, "qwen_model", "qwen3.7-plus")

    assert llm_service.QwenProvider().supports_vision() is True
