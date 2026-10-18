from aica_schema.protocol import dumps, loads, require

from ..evidence import redact
from .validation import sha256

MATERIAL_LIMIT = 96 * 1024
TEXT_MIMES = {"application/json", "text/plain", "text/markdown"}


def read_material(artifact, reader, limit=MATERIAL_LIMIT, on_material=None):
    require(limit >= 512, "Material content budget exhausted", "analysis_limit")
    maximum = 64 * 1024 * 1024
    require(artifact.get("size", 0) <= maximum, "Material exceeds artifact budget", "analysis_limit")
    raw = reader(artifact["id"], maximum)
    require(len(raw) <= maximum, "Material exceeds artifact budget", "analysis_limit")
    require(artifact["sha256"] is not None and sha256(raw) == artifact["sha256"], "Material hash mismatch", "storage_corrupt")
    text = raw.decode("utf-8-sig")
    material = redact(loads(text) if artifact["mime_type"].split(";")[0].strip() == "application/json" else text)
    if on_material:
        on_material(material)
    safe = dumps(material).encode("utf-8")
    if len(safe) <= limit:
        return material, safe
    text = safe.decode("utf-8")
    count = max(1, (limit - 384) // 12)
    middle = len(text) // 2
    material = {"selection": "partial_head_middle_tail_not_complete",
                "head": text[:count], "middle": text[middle:middle + count], "tail": text[-count:],
                "original_bytes": len(raw), "omitted_characters": max(0, len(text) - count * 3)}
    return material, dumps(material).encode("utf-8")
