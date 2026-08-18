"""Model registry service using OpenRouter API for dynamic model lists."""

import logging
from datetime import datetime, timedelta
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL = timedelta(hours=24)

# Cache storage
_models_cache: dict = {"data": None, "timestamp": None}


def _fetch_models_from_openrouter() -> list[dict]:
    """Fetch models from OpenRouter API.

    Returns:
        List of model data dictionaries
    """
    try:
        response = httpx.get(OPENROUTER_MODELS_URL, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])
    except httpx.TimeoutException:
        logger.warning("OpenRouter API request timed out")
        return []
    except httpx.HTTPStatusError as e:
        logger.warning(f"OpenRouter API returned error: {e.response.status_code}")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch models from OpenRouter: {e}")
        return []


def get_available_models(force_refresh: bool = False) -> list[dict]:
    """Get all models from OpenRouter, cached for 24 hours.

    Args:
        force_refresh: If True, bypass cache and fetch fresh data

    Returns:
        List of model data dictionaries
    """
    global _models_cache

    # Check cache validity
    if not force_refresh and _models_cache["data"] is not None:
        if _models_cache["timestamp"] and datetime.now() - _models_cache["timestamp"] < CACHE_TTL:
            return _models_cache["data"]

    # Fetch fresh data
    models = _fetch_models_from_openrouter()

    if models:
        _models_cache["data"] = models
        _models_cache["timestamp"] = datetime.now()

    return models or _models_cache.get("data", [])


def get_vision_models() -> list[dict]:
    """Get models that support image input.

    Returns:
        List of vision-capable model data dictionaries
    """
    models = get_available_models()
    vision_models = []

    for model in models:
        input_modalities = model.get("architecture", {}).get("input_modalities", [])
        if "image" in input_modalities:
            vision_models.append(model)

    return vision_models


def get_models_by_provider(provider: str) -> list[dict]:
    """Get models filtered by provider prefix.

    Args:
        provider: Provider name (openai, anthropic, google, etc.)

    Returns:
        List of model data dictionaries for the specified provider
    """
    models = get_available_models()
    provider_lower = provider.lower()

    # Provider prefix mapping for OpenRouter model IDs
    provider_prefixes = {
        "openai": ["openai/"],
        "anthropic": ["anthropic/"],
        "google": ["google/"],
        "qwen": ["qwen/"],
        "deepseek": ["deepseek/"],
        "zhipu": ["z-ai/"],  # Zhipu GLM models are under "z-ai" on OpenRouter
    }

    prefixes = provider_prefixes.get(provider_lower, [f"{provider_lower}/"])

    return [m for m in models if any(m.get("id", "").startswith(prefix) for prefix in prefixes)]


def get_vision_models_by_provider(provider: str) -> list[dict]:
    """Get vision-capable models for a specific provider.

    Args:
        provider: Provider name (openai, anthropic, google, etc.)

    Returns:
        List of vision-capable model data dictionaries for the provider
    """
    provider_models = get_models_by_provider(provider)

    return [
        m for m in provider_models
        if "image" in m.get("architecture", {}).get("input_modalities", [])
    ]


def format_model_for_api(model: dict) -> dict:
    """Format OpenRouter model data for API response.

    Args:
        model: Raw model data from OpenRouter

    Returns:
        Formatted model dictionary for API response
    """
    input_modalities = model.get("architecture", {}).get("input_modalities", [])

    return {
        "id": model.get("id", ""),
        "name": model.get("name", model.get("id", "")),
        "vision": "image" in input_modalities,
        "context_length": model.get("context_length"),
        "pricing": {
            "prompt": model.get("pricing", {}).get("prompt"),
            "completion": model.get("pricing", {}).get("completion"),
        },
    }


def get_formatted_models_for_provider(
    provider: str,
    vision_only: bool = False
) -> list[dict]:
    """Get formatted model list for a provider.

    Args:
        provider: Provider name (openai, anthropic, google, etc.)
        vision_only: If True, only return vision-capable models

    Returns:
        List of formatted model dictionaries ready for API response
    """
    if vision_only:
        models = get_vision_models_by_provider(provider)
    else:
        models = get_models_by_provider(provider)

    return [format_model_for_api(m) for m in models]


# Native model IDs that can be sent directly to each provider SDK.
FALLBACK_MODELS = {
    "openai": [
        {"id": "gpt-4o", "name": "GPT-4o", "vision": True},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "vision": True},
        {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "vision": True},
        {"id": "o1", "name": "o1", "vision": True},
        {"id": "o1-mini", "name": "o1 Mini", "vision": True},
    ],
    "anthropic": [
        {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "vision": True},
        {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "vision": True},
        {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "vision": True},
        {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "vision": True},
    ],
    "google": [
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "vision": True},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "vision": True},
        {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "vision": True},
    ],
    "qwen": [
        {"id": "qwen3.7-plus", "name": "Qwen 3.7 Plus", "vision": True},
        {"id": "qwen3-vl-plus", "name": "Qwen 3 VL Plus", "vision": True},
        {"id": "qwen3-vl-flash", "name": "Qwen 3 VL Flash", "vision": True},
        {"id": "qwen3.5-ocr", "name": "Qwen 3.5 OCR", "vision": True},
        {"id": "qwen2.5-vl-72b-instruct", "name": "Qwen 2.5 VL 72B", "vision": True},
        {"id": "qwen-vl-plus", "name": "Qwen VL Plus", "vision": True},
        {"id": "qwen-turbo", "name": "Qwen Turbo", "vision": False},
    ],
    "deepseek": [
        {"id": "deepseek-chat", "name": "DeepSeek Chat", "vision": False},
        {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "vision": False},
    ],
    "zhipu": [
        {"id": "glm-4v-plus", "name": "GLM-4V Plus (视觉增强)", "vision": True},
        {"id": "glm-4v", "name": "GLM-4V (视觉)", "vision": True},
        {"id": "glm-4-plus", "name": "GLM-4 Plus (增强)", "vision": False},
        {"id": "glm-4-flash", "name": "GLM-4 Flash (快速)", "vision": False},
        {"id": "glm-4-air", "name": "GLM-4 Air (经济)", "vision": False},
        {"id": "glm-4", "name": "GLM-4 (标准)", "vision": False},
    ],
}


def get_models_with_fallback(
    provider: str,
    vision_only: bool = False
) -> list[dict]:
    """Return native model IDs that can be sent to the provider SDK."""
    fallback = FALLBACK_MODELS.get(provider.lower(), [])
    if vision_only:
        return [m for m in fallback if m.get("vision", False)]
    return fallback
