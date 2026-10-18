from __future__ import annotations

import copy
import math
from typing import Any, Mapping

from .contracts import ExecutionContext, PreviewError, PreviewSettings, StageProfile, digest


class StageProfilePolicy:
    def __init__(self, settings: PreviewSettings):
        self.settings = settings

    def resolve(self, node: Mapping[str, Any], context: ExecutionContext) -> StageProfile:
        if context.mode not in {"draft", "high_quality", "final"}:
            raise PreviewError("invalid_stage", "Expected draft, high_quality or final")
        execution = node["execution"]
        params = copy.deepcopy(execution.get("params", {}))
        width, height = params.get("width", 768), params.get("height", 512)
        if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (width, height)):
            raise PreviewError("invalid_dimensions", "width and height must be positive integers")
        if not 1 / 8 <= width / height <= 8:
            raise PreviewError("invalid_dimensions", "Aspect ratio must be between 1:8 and 8:1")
        divisor = math.gcd(width, height)
        unit_width, unit_height = width // divisor, height // divisor
        draft = context.mode == "draft"
        limit = self.settings.draft_long_edge if draft else self.settings.hq_long_edge
        alignment = math.lcm(8 // math.gcd(unit_width, 8), 8 // math.gcd(unit_height, 8))
        multiplier = (limit // max(unit_width, unit_height) // alignment) * alignment
        if multiplier == 0:
            raise PreviewError("invalid_dimensions", "Aspect ratio cannot fit stage limits with 8-pixel alignment")
        width, height = unit_width * multiplier, unit_height * multiplier
        seed = context.seed if context.seed is not None else params.get("seed")
        if seed is None:
            seed = int(digest({"node": node["id"], "inputs": dict(context.resolved_inputs)})[:8], 16)
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
            raise PreviewError("invalid_seed", "seed must be an unsigned 32-bit integer")
        model = (self.settings.draft_model if draft else self.settings.hq_model) or execution.get("model") or ""
        params.update(
            width=width,
            height=height,
            seed=seed,
            steps=(self.settings.draft_steps if draft else self.settings.hq_steps),
        )
        return StageProfile(
            mode="draft" if draft else "high_quality",
            schema_stage="draft" if draft else "final",
            cache_suffix="draft" if draft else "hq",
            seed=seed,
            width=width,
            height=height,
            model=model,
            params=params,
        )


def resolve_stage_profile(
    node: Mapping[str, Any], context: ExecutionContext, settings: PreviewSettings | None = None
) -> StageProfile:
    return StageProfilePolicy(settings or PreviewSettings.from_env()).resolve(node, context)
