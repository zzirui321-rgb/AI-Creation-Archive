import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aica_runtime.graph.contract import GraphDocument, GraphEditRequest, LayoutUpdate, ModelProposal

from graph_contract_design.assembly import assemble
from graph_contract_design.cases import make_case, make_proposal

OUTPUT = Path(__file__).resolve().parents[2] / "design" / "graph-v0.1"
MODELS = (GraphDocument, GraphEditRequest, LayoutUpdate, ModelProposal)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def ts_type(schema):
    if "$ref" in schema:
        return schema["$ref"].split("/")[-1]
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(item, ensure_ascii=False) for item in schema["enum"])
    alternatives = schema.get("anyOf", schema.get("oneOf"))
    if alternatives:
        return "(" + " | ".join(ts_type(item) for item in alternatives) + ")"
    kind = schema.get("type")
    if kind == "array":
        return "Array<" + ts_type(schema["items"]) + ">"
    if kind == "object":
        required = set(schema.get("required", []))
        fields = [json.dumps(key) + ("" if key in required else "?") + ": " + ts_type(value) + ";"
                  for key, value in schema.get("properties", {}).items()]
        extra = schema.get("additionalProperties")
        if extra is True or isinstance(extra, dict):
            fields.append("[key: string]: " + ("unknown" if extra is True else ts_type(extra)) + ";")
        return "{ " + " ".join(fields) + " }"
    if kind in {"integer", "number"}:
        return "number"
    if kind in {"string", "boolean", "null"}:
        return kind
    if not schema:
        return "unknown"
    raise ValueError("Unsupported schema in TypeScript generator: " + str(schema))


def artifacts():
    generated = {}
    definitions = {}
    assertions = ['import type { GraphDocument, GraphEditRequest, LayoutUpdate } from "../graph.generated";']
    for model in MODELS:
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = "urn:aica:semantic-design:0.1:" + model.__name__
        generated[model.__name__ + ".schema.json"] = json_text(schema)
        for name, definition in schema.get("$defs", {}).items():
            if name in definitions and definitions[name] != definition:
                raise ValueError("Conflicting generated definition " + name)
            definitions[name] = definition
        definitions[model.__name__] = {key: value for key, value in schema.items() if key != "$defs"}
    generated["graph.generated.ts"] = "\n".join(
        "export type " + name + " = " + ts_type(definition) + ";" for name, definition in sorted(definitions.items())) + "\n"
    for name in ("multiturn", "parallel", "partial"):
        case = make_case(name)
        generated[f"examples/{name}.aica.json"] = case["archive_bytes"].decode()
        generated[f"examples/{name}.events.jsonl"] = case["history"]["fixture-history"].decode()
        generated[f"examples/{name}.graph.json"] = json_text(case["graph"])
        assertions.append("export const " + name + ": GraphDocument = " + json_text(case["graph"]).rstrip() + ";")
    generated["examples/model-proposal.json"] = json_text(make_proposal(make_case()))
    generated["examples/edit-request.json"] = json_text({"request_id": "edit-request-1", "expected_revision_id": "semantic-revision-1",
                                                        "operations": [{"op": "set_label", "node_id": "step-character", "value": "角色参考图"}]})
    generated["examples/layout-request.json"] = json_text({"request_id": "layout-request-1", "graph_revision_id": "semantic-revision-1",
                                                          "expected_layout_version": 0, "positions": [{"node_id": "step-character", "x": 200.0, "y": 80.0}]})
    assertions.append("export const edit: GraphEditRequest = " + generated["examples/edit-request.json"].rstrip() + ";")
    assertions.append("export const layout: LayoutUpdate = " + generated["examples/layout-request.json"].rstrip() + ";")
    generated["examples/contract.typecheck.ts"] = "\n".join(assertions) + "\n"
    probe = OUTPUT / "evidence" / "hybrid-v2.json"
    if probe.is_file():
        recorded = json.loads(probe.read_text(encoding="utf-8"))
        graph = assemble(recorded["proposal"], make_case("parallel"), recorded["model"])
        generated["evidence/hybrid-v2.normalized.graph.json"] = json_text(graph)
    return generated


if __name__ == "__main__":
    for relative, content in artifacts().items():
        target = OUTPUT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"generated_files": len(artifacts()), "directory": "packages/runtime/design/graph-v0.1"}))
