"""Normalize Panel consistency inputs without inventing Graph dependencies."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import PreviewError, digest

REFERENCE_KEYS = (
    "character_ref",
    "character_artifact_id",
    "style_ref",
    "style_artifact_id",
    "background_ref",
    "background_artifact_id",
    "environment_ref",
    "environment_artifact_id",
)
REFERENCE_LIST_KEY = "reference_artifact_ids"
CONTEXT_KEY = "consistency_context"


def _bounded_string(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise PreviewError(
            "invalid_consistency_context", f"{field} must be a non-empty string of at most 512 characters"
        )
    return value.strip()


def normalize_consistency_inputs(inputs: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]]):
    """Return inputs plus verified role references and a stable context identity."""
    normalized = dict(inputs)
    raw = normalized.get(CONTEXT_KEY, {})
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise PreviewError("invalid_consistency_context", "consistency_context must be an object")

    context = {
        "identity_hash": _bounded_string(raw.get("identity_hash"), "identity_hash", required=True),
    }
    for key in ("camera", "composition", "shot", "framing", "style_version"):
        value = _bounded_string(raw.get(key), key)
        if value is not None:
            context[key] = value
    seed = raw.get("seed")
    if seed is not None:
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**32:
            raise PreviewError(
                "invalid_consistency_context", "consistency_context.seed must be an unsigned 32-bit integer"
            )
        context["seed"] = seed

    references = list(normalized.get(REFERENCE_LIST_KEY, []))
    if not isinstance(references, list) or any(not isinstance(item, str) for item in references):
        raise PreviewError("invalid_reference", "reference_artifact_ids must be a list of artifact IDs")
    roles: dict[str, str] = {}
    for key in REFERENCE_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value not in artifacts:
            raise PreviewError("missing_consistency_reference", f"{key} must reference an available image Artifact")
        artifact = artifacts[value]
        if not str(artifact.get("mime_type", "")).startswith("image/"):
            raise PreviewError("invalid_consistency_reference", f"{key} must reference an image Artifact")
        role = key.removesuffix("_artifact_id").removesuffix("_ref")
        roles[role] = value
        references.append(value)
    for identifier in references:
        if identifier not in artifacts:
            raise PreviewError("invalid_reference", f"Unknown reference Artifact {identifier}")
        if not str(artifacts[identifier].get("mime_type", "")).startswith("image/"):
            raise PreviewError("invalid_reference", "Consistency references must be images")
    references = list(dict.fromkeys(references))
    if not references:
        raise PreviewError(
            "missing_consistency_reference", "A consistency context requires at least one reference Artifact"
        )
    context["reference_roles"] = roles
    context["reference_artifact_ids"] = references
    context["context_hash"] = digest({key: value for key, value in context.items() if key != "reference_artifact_ids"})
    normalized[REFERENCE_LIST_KEY] = references
    normalized[CONTEXT_KEY] = context
    return normalized, context


def consistency_seed(context: Mapping[str, Any], current: int | None):
    requested = context.get("seed")
    if requested is not None and current is not None and requested != current:
        raise PreviewError("consistency_seed_conflict", "Panel seed conflicts with consistency_context.seed")
    return requested if requested is not None else current


def ensure_reference_consistency_context(inputs: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]]):
    """Give legacy reference-only Panel calls a deterministic continuity identity."""
    normalized = dict(inputs)
    if CONTEXT_KEY in normalized:
        return normalized, normalized[CONTEXT_KEY]
    references = normalized.get(REFERENCE_LIST_KEY, [])
    if isinstance(references, str):
        references = [references]
    if not isinstance(references, list) or not references:
        return normalized, None
    hashes = []
    for identifier in references:
        if identifier not in artifacts:
            raise PreviewError("invalid_reference", f"Unknown reference Artifact {identifier}")
        if not str(artifacts[identifier].get("mime_type", "")).startswith("image/"):
            raise PreviewError("invalid_reference", "Consistency references must be images")
        hashes.append(artifacts[identifier]["sha256"])
    context = {"identity_hash": "references-" + digest({"artifacts": hashes}), "reference_artifact_ids": references}
    context["context_hash"] = digest(context)
    normalized[REFERENCE_LIST_KEY] = references
    normalized[CONTEXT_KEY] = context
    return normalized, context
