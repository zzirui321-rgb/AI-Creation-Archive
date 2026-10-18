"""Target proposals bind semantic masks to immutable source image content."""

from __future__ import annotations

import asyncio
import base64
import binascii
import copy
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass, field, replace
from typing import Literal

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .artifact_store import atomic_write
from .contracts import PreviewError, canonical_json, digest
from .scene_layers import decode, png


class EditIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation: Literal["replace", "attribute", "background", "append"]
    instruction: str = Field(min_length=1, max_length=20000)
    targets: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("instruction")
    @classmethod
    def instruction_not_blank(cls, value):
        if not value.strip():
            raise ValueError("An edit instruction is required")
        return value.strip()

    @field_validator("targets")
    @classmethod
    def bounded_targets(cls, values):
        if any(not item.strip() or len(item) > 200 for item in values):
            raise ValueError("Each target must contain 1..200 characters")
        return list(dict.fromkeys(item.strip() for item in values))


@dataclass(frozen=True)
class SegmentationSettings:
    endpoint: str = ""
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 120
    max_bytes: int = 20 * 1024 * 1024

    def __post_init__(self):
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("Segmentation timeout must be within (0, 300] seconds")
        if (
            isinstance(self.max_bytes, bool)
            or not isinstance(self.max_bytes, int)
            or not 0 < self.max_bytes <= 28 * 1024 * 1024
        ):
            raise ValueError("Invalid segmentation response byte limit")

    @classmethod
    def from_env(cls):
        return cls(
            endpoint=os.environ.get("AICA_SEGMENT_API_URL", ""), api_key=os.environ.get("AICA_SEGMENT_API_KEY", "")
        )


class HTTPSegmenter:
    def __init__(self, settings=None, *, transport=None):
        self.settings = settings or SegmentationSettings.from_env()
        self.transport = transport

    async def segment(self, data, targets):
        from .providers.live_api import checked_url

        if not self.settings.endpoint:
            raise PreviewError("segmentation_unconfigured", "Configure a semantic segmentation service")
        headers = {"Authorization": "Bearer " + self.settings.api_key} if self.settings.api_key else {}
        try:
            async with (
                asyncio.timeout(self.settings.timeout_seconds),
                httpx.AsyncClient(
                    transport=self.transport, timeout=self.settings.timeout_seconds, trust_env=False
                ) as client,
            ):
                async with client.stream(
                    "POST",
                    checked_url(self.settings.endpoint),
                    headers=headers,
                    json={
                        "image_base64": base64.b64encode(data).decode(),
                        "targets": targets,
                    },
                ) as response:
                    response.raise_for_status()
                    chunks, length = [], 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > self.settings.max_bytes:
                            raise PreviewError("segmentation_oversized", "Segmentation response exceeds byte limit")
                        chunks.append(chunk)
            body = json.loads(b"".join(chunks))
        except (TimeoutError, httpx.HTTPError, ValueError, RecursionError) as error:
            raise PreviewError("segmentation_unavailable", "Semantic segmentation service unavailable") from error
        return body


def proposal_id(value):
    return "edit-" + digest({key: item for key, item in value.items() if key != "id"})


def save_proposal(store, proposal):
    proposal["id"] = proposal_id(proposal)
    atomic_write(store.root / "edit-proposals" / (proposal["id"] + ".json"), canonical_json(proposal))
    return proposal


def source_image(engine, artifact):
    engine.contract.validate("artifact", artifact)
    if artifact["mime_type"] not in {"image/png", "image/jpeg", "image/webp"}:
        raise PreviewError("invalid_edit_input", "Semantic editing requires a raster image")
    data = engine.store.read(artifact)
    return data, decode(data)


def add_candidate(engine, source, mask, label, score, provenance):
    mask = mask.convert("L")
    box = mask.getbbox()
    if not box or mask.histogram()[0] == 0:
        raise PreviewError("invalid_semantic_mask", "A mask must contain both editable and protected pixels")
    artifact = engine.store.put(png(mask), "image/png", node_id=None, run_id=None)
    engine.contract.validate("artifact", artifact)
    identity = {"source": source["sha256"], "mask": artifact["sha256"], "provenance": provenance}
    return {
        "id": "target-" + digest(identity),
        "label": label,
        "score": score,
        "mask_artifact": artifact,
        "rect": [
            box[0] / mask.width,
            box[1] / mask.height,
            (box[2] - box[0]) / mask.width,
            (box[3] - box[1]) / mask.height,
        ],
        "editable_pixel_count": mask.width * mask.height - mask.histogram()[0],
        "provenance": provenance,
    }


def validated_segments(body, size, source_hash, targets):
    if not isinstance(body, dict) or body.get("source_sha256") != source_hash:
        raise PreviewError("segmentation_source_mismatch", "Segmentation response does not match the original image")
    model = body.get("model")
    if (
        not isinstance(model, dict)
        or not model
        or any(
            not isinstance(key, str) or not isinstance(value, str) or not 0 < len(value) <= 300
            for key, value in model.items()
        )
    ):
        raise PreviewError("segmentation_identity_missing", "Segmentation must report model and version identities")
    if not {"detector", "detector_revision", "segmenter", "segmenter_revision"} <= model.keys():
        raise PreviewError("segmentation_identity_missing", "Detector and segmenter revisions are required")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > 32:
        raise PreviewError("invalid_segmentation", "Expected at most 32 segmentation candidates")
    result, seen = [], set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise PreviewError("invalid_segmentation", "Invalid segmentation candidate")
        label, score, encoded = candidate.get("label"), candidate.get("score"), candidate.get("mask_base64")
        target = candidate.get("target")
        if target not in targets:
            raise PreviewError("invalid_segmentation", "Each mask must identify its requested target")
        if (
            not isinstance(label, str)
            or not 0 < len(label) <= 200
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise PreviewError("invalid_segmentation", "Invalid label or detector score")
        if not isinstance(encoded, str) or len(encoded) > 4 * 1024 * 1024:
            raise PreviewError("invalid_semantic_mask", "Invalid encoded mask")
        try:
            data = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.size != size:
                    raise PreviewError(
                        "invalid_semantic_mask", "Segmentation masks must be PNGs at original resolution"
                    )
            mask_image = decode(data)
        except (binascii.Error, OSError, ValueError) as error:
            raise PreviewError("invalid_semantic_mask", "Invalid PNG segmentation mask") from error
        mask = ImageChops.multiply(mask_image.convert("L"), mask_image.getchannel("A")).point(
            lambda value: 255 if value >= 128 else 0
        )
        identity = hashlib.sha256(mask.tobytes()).hexdigest()
        if identity not in seen and mask.getbbox():
            result.append((label, float(score), mask, target))
            seen.add(identity)
    return result, {"source": "semantic_segmentation", **model}


def placement_masks(image, protected):
    width, height = image.size
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    choices = [
        ("Upper right", 0.68, 0.04),
        ("Upper left", 0.04, 0.04),
        ("Lower right", 0.68, 0.58),
        ("Lower left", 0.04, 0.58),
    ]
    ranked = []
    for label, x, y in choices:
        box = (round(x * width), round(y * height), round((x + 0.28) * width), round((y + 0.38) * height))
        if protected.crop(box).getbbox():
            continue
        mask = Image.new("L", image.size)
        ImageDraw.Draw(mask).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
        ranked.append((ImageStat.Stat(edges.crop(box)).mean[0], label, mask))
    return sorted(ranked, key=lambda item: item[0])


async def propose_image_edit(engine, original, intent, *, segmenter=None):
    intent = intent if isinstance(intent, EditIntent) else EditIntent.model_validate(intent)
    data, image = source_image(engine, original)
    proposal = {
        "format": "aica-edit-proposal/1",
        "source_sha256": original["sha256"],
        "width": image.width,
        "height": image.height,
        "intent": intent.model_dump(),
        "status": "not_found",
        "reason": "No matching target",
        "candidates": [],
    }
    segments, provenance = [], {}
    if intent.targets:
        try:
            body = await (segmenter or engine.segmenter).segment(data, intent.targets)
            segments, provenance = validated_segments(body, image.size, original["sha256"], intent.targets)
        except PreviewError as error:
            proposal.update(status="unavailable", reason=error.code)
            return save_proposal(engine.store, proposal)
    elif intent.operation != "append":
        proposal.update(status="needs_target", reason="Describe the object to edit or preserve")
        return save_proposal(engine.store, proposal)
    if intent.operation in {"background", "append"} and intent.targets:
        missing = set(intent.targets) - {item[3] for item in segments}
        if missing:
            proposal.update(reason="Unresolved protected targets: " + ", ".join(sorted(missing)))
            return save_proposal(engine.store, proposal)
    if intent.operation == "append":
        if intent.targets and not segments:
            return save_proposal(engine.store, proposal)
        protected = Image.new("L", image.size)
        for _, _, mask, _ in segments:
            protected = ImageChops.lighter(protected, mask)
        for _, label, mask in placement_masks(image, protected):
            proposal["candidates"].append(
                add_candidate(
                    engine,
                    original,
                    mask,
                    label,
                    None,
                    {
                        "source": "placement_proposal",
                        "algorithm": "edge_density_and_protected_objects/1",
                        "segmentation": provenance,
                    },
                )
            )
        proposal.update(
            status="needs_confirmation" if proposal["candidates"] else "not_found",
            reason="Confirm placement of the new object",
        )
    elif intent.operation == "background" and segments:
        protected = Image.new("L", image.size)
        for _, _, mask, _ in segments:
            protected = ImageChops.lighter(protected, mask)
        mask = protected.point(lambda value: 255 - value)
        proposal["candidates"] = [
            add_candidate(
                engine,
                original,
                mask,
                "Background; preserve all matching objects",
                min(item[1] for item in segments),
                provenance,
            )
        ]
    else:
        for label, score, mask, _ in segments:
            padding = min(31, max(1, round(min(image.size) * 0.02))) if intent.operation == "replace" else 0
            if padding:
                mask = mask.filter(ImageFilter.MaxFilter(2 * padding + 1))
            proposal["candidates"].append(
                add_candidate(
                    engine, original, mask, label, score, {**provenance, "boundary_padding_pixels": str(padding)}
                )
            )
    if proposal["candidates"] and intent.operation != "append":
        area = proposal["candidates"][0]["editable_pixel_count"] / (image.width * image.height)
        ready = len(proposal["candidates"]) == 1 and proposal["candidates"][0]["score"] >= 0.4 and 0.001 <= area <= 0.97
        proposal.update(
            status="ready" if ready else "needs_confirmation",
            reason="One bounded target" if ready else "Confirm the intended target",
        )
    return save_proposal(engine.store, proposal)


def read_proposal(store, identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r"edit-[a-f0-9]{64}", identifier):
        raise PreviewError("invalid_proposal", "Invalid edit proposal ID")
    try:
        data = (store.root / "edit-proposals" / (identifier + ".json")).read_bytes()
        if len(data) > 1024 * 1024:
            raise ValueError("Proposal exceeds byte limit")
        proposal = json.loads(data)
    except (OSError, ValueError) as error:
        raise PreviewError("proposal_unavailable", "Edit proposal unavailable") from error
    if not isinstance(proposal, dict) or proposal.get("id") != identifier or proposal_id(proposal) != identifier:
        raise PreviewError("proposal_conflict", "Edit proposal content changed")
    return proposal


async def execute_proposed_edit(
    engine, node, artifacts, *, context, original_artifact_id, proposal_id, candidate_id=None
):
    proposal = read_proposal(engine.store, proposal_id)
    if original_artifact_id not in artifacts or artifacts[original_artifact_id]["sha256"] != proposal["source_sha256"]:
        raise PreviewError("stale_edit_proposal", "Source image changed; request a new edit proposal")
    source_image(engine, artifacts[original_artifact_id])
    if proposal["status"] not in {"ready", "needs_confirmation"}:
        raise PreviewError("target_unavailable", "No confirmed editable target")
    if candidate_id is None:
        if proposal["status"] != "ready":
            raise PreviewError("target_confirmation_required", "Select one proposed target before model execution")
        candidate = proposal["candidates"][0]
    else:
        candidate = next((item for item in proposal["candidates"] if item["id"] == candidate_id), None)
        if candidate is None:
            raise PreviewError("unknown_edit_target", "Selected target does not belong to this proposal")
    mask = candidate["mask_artifact"]
    engine.store.read(mask)
    edited = copy.deepcopy(node)
    params = edited["execution"].setdefault("params", {})
    params["edit_operation"] = "overlay" if proposal["intent"]["operation"] == "append" else "replace"
    params["edit_target"] = {
        "candidate_id": candidate["id"],
        "source_sha256": proposal["source_sha256"],
        "mask_sha256": mask["sha256"],
        "intent": proposal["intent"],
        "provenance": candidate["provenance"],
    }
    inputs = {**context.resolved_inputs, "prompt": proposal["intent"]["instruction"]}
    inputs.pop("resolved_prompt", None)
    result = await engine.execute_image_edit(
        edited,
        {**artifacts, mask["id"]: mask},
        context=replace(context, resolved_inputs=inputs),
        original_artifact_id=original_artifact_id,
        mask_artifact_id=mask["id"],
    )
    result.metadata["edit_proposal"] = {
        "id": proposal["id"],
        "candidate_id": candidate["id"],
        "intent": proposal["intent"],
        "source_sha256": proposal["source_sha256"],
        "mask_artifact": mask,
        "provenance": candidate["provenance"],
        "selection": "explicit" if candidate_id else "single_target_policy",
    }
    return result
