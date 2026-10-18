from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

JsonObject = dict[str, Any]
Mode = Literal["draft", "high_quality", "final"]
Decision = Literal["CACHE_HIT", "RECOMPUTE", "REBUILD"]
EXTENSION_KEY = "aica.preview"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class PreviewError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreviewSettings:
    provider: str = "prewarmed_cache"
    api_base: str = ""
    api_key: str = field(default="", repr=False)
    api_style: str = "auto"
    draft_model: str = ""
    hq_model: str = ""
    timeout_seconds: float = 10.0
    draft_long_edge: int = 768
    hq_long_edge: int = 1024
    draft_steps: int = 4
    hq_steps: int = 28
    max_download_bytes: int = 25 * 1024 * 1024
    allow_fallback: bool = True

    def __post_init__(self):
        if self.provider not in {"auto", "prewarmed_cache", "live_api", "mock_svg", "local_fallback"}:
            raise ValueError("Unknown PREVIEW_PROVIDER")
        if self.api_style not in {"auto", "openai", "siliconflow", "fal", "qwen", "sdcpp"}:
            raise ValueError("Unknown image API style")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("Image timeout must be within (0, 300] seconds; default is 10")
        if not 64 <= self.draft_long_edge <= self.hq_long_edge <= 2048:
            raise ValueError("Stage long edges must satisfy 64 <= draft <= hq <= 2048")

    @classmethod
    def from_env(
        cls, env_file: Path | None = Path(".env"), environ: Mapping[str, str] | None = None
    ) -> PreviewSettings:
        values: dict[str, Any] = {}
        if env_file is not None:
            from dotenv import dotenv_values

            values.update(dotenv_values(env_file))
        values.update(os.environ if environ is None else environ)

        def get(primary: str, alias: str = "", default: str = "") -> str:
            return str(values.get(primary) or values.get(alias) or default)

        return cls(
            provider=get("PREVIEW_PROVIDER", default="prewarmed_cache"),
            api_base=get("AICA_IMAGE_API_BASE", "IMAGE_API_URL"),
            api_key=get("AICA_IMAGE_API_KEY", "IMAGE_API_KEY"),
            api_style=get("AICA_IMAGE_API_STYLE", default="auto"),
            draft_model=get("AICA_DRAFT_MODEL", "IMAGE_MODEL_DRAFT"),
            hq_model=get("AICA_HQ_MODEL", "IMAGE_MODEL_HQ"),
            timeout_seconds=float(get("AICA_IMAGE_TIMEOUT", default="10")),
        )


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    cache_key: str
    resolved_inputs: Mapping[str, Any] = field(default_factory=dict)
    mode: Mode = "draft"
    seed: int | None = None
    decision: Decision = "RECOMPUTE"
    node_states: Mapping[str, str] = field(default_factory=dict)
    node_prompts: Mapping[str, str] = field(default_factory=dict)
    artifact_metadata: Mapping[str, JsonObject] = field(default_factory=dict)


@dataclass(frozen=True)
class StageProfile:
    mode: Literal["draft", "high_quality"]
    schema_stage: Literal["draft", "final"]
    cache_suffix: str
    seed: int
    width: int
    height: int
    model: str
    params: JsonObject


@dataclass(frozen=True)
class ExecutionRequest:
    node: Mapping[str, Any]
    artifacts: Mapping[str, Mapping[str, Any]]
    inputs: Mapping[str, Any]
    context: ExecutionContext
    profile: StageProfile
    stage_key: str
    fingerprint: str


@dataclass
class AdapterOutput:
    data: bytes
    mime_type: str
    source: str
    metadata: JsonObject = field(default_factory=dict)
    cache_hit: bool = False


@dataclass
class PreviewResult:
    artifacts: list[JsonObject]
    outputs: JsonObject
    cache: JsonObject
    preview_stage: str
    metadata: JsonObject
    duration_ms: float

    def to_dict(self) -> JsonObject:
        return asdict(self)

    def extension_patch(self, run_id: str) -> JsonObject:
        return {EXTENSION_KEY: {"runs": {run_id: self.metadata}}}


@dataclass(frozen=True)
class PlanStep:
    node: Mapping[str, Any]
    context: ExecutionContext
    depends_on: tuple[str, ...] = ()


@dataclass
class PlanResult:
    artifacts: dict[str, JsonObject]
    outputs: dict[str, JsonObject]
    node_results: dict[str, PreviewResult]
    resolved_inputs: dict[str, JsonObject]
    cache_by_stage: dict[str, dict[str, JsonObject]]
    statistics: JsonObject
    duration_ms: float

    def to_dict(self) -> JsonObject:
        return asdict(self)


class PlanExecutionError(PreviewError):
    def __init__(self, node_id: str, partial_result: PlanResult, code: str):
        super().__init__(code, f"Plan stopped at node {node_id}; completed results are retained")
        self.failed_node_id = node_id
        self.partial_result = partial_result
