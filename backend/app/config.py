from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path
from typing import Optional
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"

LLM_ENV_KEYS = frozenset({
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "GOOGLE_API_KEY",
    "GOOGLE_MODEL",
    "GOOGLE_BASE_URL",
    "QWEN_API_KEY",
    "QWEN_MODEL",
    "QWEN_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_MODEL",
    "DEEPSEEK_BASE_URL",
    "ZHIPU_API_KEY",
    "ZHIPU_MODEL",
    "ZHIPU_BASE_URL",
})

PROVIDER_ENV_KEYS = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL"),
    "google": ("GOOGLE_API_KEY", "GOOGLE_MODEL", "GOOGLE_BASE_URL"),
    "qwen": ("QWEN_API_KEY", "QWEN_MODEL", "QWEN_BASE_URL"),
    "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"),
    "zhipu": ("ZHIPU_API_KEY", "ZHIPU_MODEL", "ZHIPU_BASE_URL"),
}

_env_file_path_override: Path | None = None
_last_llm_env_mtime: float | None = None


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/invoice_db"

    # LLM Provider Configuration
    # Supported providers: openai, anthropic, google, qwen, deepseek, zhipu
    llm_provider: str = ""  # Active provider (empty = not configured)

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = ""  # Custom base URL (for OpenAI-compatible APIs)

    # Anthropic (Claude)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-haiku-20240307"
    anthropic_base_url: str = ""  # Custom base URL (e.g., for proxy)

    # Google (Gemini)
    google_api_key: str = ""
    google_model: str = "gemini-1.5-flash"
    google_base_url: str = ""  # Custom base URL (e.g., for proxy)

    # Qwen (Alibaba)
    qwen_api_key: str = ""
    qwen_model: str = "qwen3.7-plus"
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com"

    # Zhipu (GLM)
    zhipu_api_key: str = ""
    zhipu_model: str = "glm-4-flash"
    zhipu_base_url: str = ""  # Custom base URL (e.g., for proxy)

    # File upload
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    allowed_extensions: list[str] = ["pdf", "jpg", "jpeg", "png"]

    # Monthly document batch upload (kept separate from legacy invoice upload)
    batch_max_file_size: int = 150 * 1024 * 1024
    batch_max_total_size: int = 256 * 1024 * 1024
    batch_max_files: int = 200
    batch_allowed_extensions: list[str] = [
        "pdf", "jpg", "jpeg", "png", "mp4", "mov", "avi", "mkv"
    ]
    batch_image_max_pixels: int = 50_000_000
    batch_processing_concurrency: int = 2
    batch_classification_threshold: float = 0.80
    batch_extraction_threshold: float = 0.75
    package_max_files: int = 600
    package_max_total_size: int = 768 * 1024 * 1024

    # Parallel processing settings
    # Separate pools for OCR and LLM to maximize throughput
    # OCR is CPU-bound (image processing), limit based on available cores
    # LLM is I/O-bound (API calls), can handle more concurrent requests
    ocr_max_workers: int = 8   # CPU-bound: ~2x typical core count
    llm_max_workers: int = 15  # I/O-bound: limited by API rate limits
    llm_batch_concurrency: int = 2

    # App
    debug: bool = True

    # Optional: require token to configure LLM via API (X-LLM-Config-Token)
    llm_config_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def get_active_llm_provider(self) -> Optional[str]:
        """Get the active LLM provider based on configuration."""
        # If explicitly set, use that
        if self.llm_provider:
            return self.llm_provider

        # Auto-detect based on available API keys
        if self.openai_api_key:
            return "openai"
        if self.anthropic_api_key:
            return "anthropic"
        if self.google_api_key:
            return "google"
        if self.qwen_api_key:
            return "qwen"
        if self.deepseek_api_key:
            return "deepseek"
        if self.zhipu_api_key:
            return "zhipu"

        return None

    def is_llm_configured(self) -> bool:
        """Check if any LLM provider is configured."""
        return self.get_active_llm_provider() is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache():
    """Clear the settings cache to reload from environment."""
    get_settings.cache_clear()


def get_env_file_path() -> Path:
    return _env_file_path_override or ENV_FILE_PATH


def set_env_file_path_for_tests(path: Path | None) -> None:
    """Point LLM .env reload at a temp file during tests."""
    global _env_file_path_override, _last_llm_env_mtime
    _env_file_path_override = path
    _last_llm_env_mtime = None


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def update_env_file(updates: dict[str, str], env_path: Path | None = None) -> None:
    """Update .env key-value pairs with an atomic replace.

    Preserves existing entries and comments, updates existing keys,
    and appends new keys at the end.
    """
    path = env_path or get_env_file_path()
    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}\n")
                updated_keys.add(key)
            else:
                new_lines.append(line if line.endswith("\n") else f"{line}\n")
        else:
            new_lines.append(line if line.endswith("\n") else f"{line}\n")

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(new_lines)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    logger.info("Updated .env file with keys: %s", list(updates.keys()))


def apply_llm_env_from_file(env_path: Path | None = None) -> dict[str, str]:
    """Copy LLM keys from .env onto os.environ, overriding process/Docker values."""
    path = env_path or get_env_file_path()
    parsed = parse_env_file(path)
    applied: dict[str, str] = {}
    for key in LLM_ENV_KEYS:
        if key in parsed:
            os.environ[key] = parsed[key]
            applied[key] = parsed[key]
    return applied


def build_llm_env_updates(
    provider: str,
    *,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
    existing_api_key: str,
) -> dict[str, str]:
    """Build .env updates for switching the active recognition model."""
    if provider not in PROVIDER_ENV_KEYS:
        raise ValueError(f"不支持的LLM提供商: {provider}")
    key_name, model_name, base_url_name = PROVIDER_ENV_KEYS[provider]
    updates: dict[str, str] = {"LLM_PROVIDER": provider}
    trimmed_key = (api_key or "").strip()
    if trimmed_key:
        updates[key_name] = trimmed_key
    elif not (existing_api_key or "").strip():
        raise ValueError("API密钥不能为空")
    if model:
        updates[model_name] = model
    if base_url:
        updates[base_url_name] = base_url
    return updates


def reload_llm_from_env_file(*, force: bool = False, env_path: Path | None = None) -> bool:
    """Reload LLM settings from .env when the file mtime changes.

    Returns True if settings were reapplied.
    """
    global _last_llm_env_mtime
    path = env_path or get_env_file_path()
    if not path.exists():
        return False
    mtime = path.stat().st_mtime
    if not force and _last_llm_env_mtime is not None and mtime == _last_llm_env_mtime:
        return False
    apply_llm_env_from_file(path)
    clear_settings_cache()
    from app.services.llm_service import reset_llm_service
    reset_llm_service()
    _last_llm_env_mtime = mtime
    return True
