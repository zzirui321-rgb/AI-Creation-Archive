import json

from ..contracts import digest


def image_prompt(request):
    """Include resolved semantic values without sending internal artifact identities."""
    prompt = str(request.inputs.get("resolved_prompt", request.inputs.get("prompt", "")))

    def semantic(value):
        if isinstance(value, str) and value in request.artifacts:
            return None
        if isinstance(value, dict):
            return {key: semantic(item) for key, item in value.items() if key not in {"reference_artifact_ids"}}
        if isinstance(value, list):
            return [semantic(item) for item in value]
        return value

    context = {
        key: semantic(value)
        for key, value in request.inputs.items()
        if key
        not in {
            "prompt",
            "resolved_prompt",
            "reference_artifact_ids",
            "original_image",
            "edit_mask",
            "edit_target_identity",
        }
    }
    context = {key: value for key, value in context.items() if value not in (None, {}, [], "")}
    if context:
        prompt += "\n\nCreative parameters:\n" + json.dumps(context, ensure_ascii=False, sort_keys=True)
    consistency = request.inputs.get("consistency_context")
    if not isinstance(consistency, dict) and request.inputs.get("reference_artifact_ids"):
        consistency = {
            "identity_hash": "references-"
            + digest(
                {"artifacts": [request.artifacts[item]["sha256"] for item in request.inputs["reference_artifact_ids"]]}
            )
        }
    if isinstance(consistency, dict):
        stable = {
            key: consistency[key]
            for key in ("identity_hash", "camera", "composition", "shot", "framing", "style_version")
            if consistency.get(key) is not None
        }
        if stable:
            prompt += "\n\nContinuity constraints:\n" + json.dumps(stable, ensure_ascii=False, sort_keys=True)
        prompt += "\nPreserve the referenced character, style and environment identity. Keep the requested camera and composition unless the edit explicitly changes them."
    role = request.node["execution"].get("params", {}).get("layer_role")
    if request.profile.params.get("edit_mode") == "masked_replace":
        if request.profile.params.get("edit_operation") == "overlay":
            prompt += "\nRender only this new object, isolated on a pure white studio background, entire silhouette visible, generous empty margins, no scenery, no text, no shadows."
        elif request.profile.params.get("edit_reference", True):
            prompt += "\nEdit the supplied original image according to the requested changes. Preserve its composition and all unrelated content."
    elif role in {"subject", "detail"}:
        prompt += "\nIsolated single subject on a pure white studio background, entire silhouette visible, generous empty margin, no scenery, no floor, no shadows, no text."
    elif role == "background":
        prompt += "\nEmpty environment plate only, no main character, no foreground props, preserve space for layered subjects."
    return prompt.strip()
