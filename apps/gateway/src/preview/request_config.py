"""Ephemeral user-selected provider settings; never persist API credentials."""

from dataclasses import replace
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .contracts import PreviewSettings, digest


class ImageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["custom", "openai", "siliconflow", "fal", "qwen", "sdcpp", "local_fallback"]
    api_base: str = Field(default="", max_length=2048)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False, max_length=512)
    model: str = Field(default="", max_length=200)
    draft_model: str = Field(default="", max_length=200)
    hq_model: str = Field(default="", max_length=200)
    timeout_seconds: float = Field(default=10, gt=0, le=300)
    allow_fallback: bool = True

    @field_validator("api_base")
    @classmethod
    def endpoint(cls, value):
        if value:
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Expected an HTTP(S) model endpoint without embedded credentials or query")
        return value

    def settings(self, defaults=None):
        defaults = defaults or PreviewSettings.from_env()
        if self.provider == "local_fallback":
            return replace(defaults, provider="local_fallback", api_key="", api_base="")
        if (
            not self.api_base
            or not self.api_key.get_secret_value()
            or not (self.model or self.draft_model and self.hq_model)
        ):
            raise ValueError("Configure provider URL, API key and model before execution")
        return replace(
            defaults,
            provider="prewarmed_cache",
            api_style="auto" if self.provider == "custom" else self.provider,
            api_base=self.api_base,
            api_key=self.api_key.get_secret_value(),
            draft_model=self.draft_model or self.model,
            hq_model=self.hq_model or self.model,
            timeout_seconds=self.timeout_seconds,
            allow_fallback=self.allow_fallback,
        )

    def public_identity(self):
        settings = self.settings()
        return digest({key: value for key, value in vars(settings).items() if key != "api_key"})


def configured_registry(config, default):
    if config is None:
        return default
    from aica_runtime.executors import default_registry

    from .runtime_bridge import register_preview_tools

    return register_preview_tools(default_registry(), config.settings())
