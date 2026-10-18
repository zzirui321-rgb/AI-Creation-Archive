"""Synthetic acceptance inputs only; no business fixtures are imported by the engine."""

import argparse
from pathlib import Path

from aica_schema.protocol import dumps, empty_archive, require_archive
from preview.runtime_bridge import BRIDGE_VERSION


def literal(value):
    return {"kind": "literal", "value": value}


def variable(name):
    return {"kind": "variable", "variable_id": name}


def output(identifier):
    return {"kind": "node_output", "node_id": identifier, "output": "out"}


def template(text, **variables):
    return {"kind": "template", "template": text, "variables": variables}


def node(identifier, tool, inputs, provenance="recorded", params=None):
    return {
        "id": identifier,
        "label": identifier.replace("_", " ").title(),
        "kind": "composition" if tool == "layout_compose" else "model",
        "branch_id": "main",
        "provenance": {"mode": provenance, "evidence": [{"source": "Synthetic acceptance fixture / " + identifier}]},
        "execution": {
            "kind": "model",
            "tool": tool,
            "version": BRIDGE_VERSION,
            "inputs": inputs,
            "params": params or {"seed": 410},
            "outputs": ["out"],
        },
    }


def finish(archive, values):
    for identifier, value in values.items():
        bindings = []
        for item in archive["graph"]["nodes"]:
            for port, binding in item.get("execution", {}).get("inputs", {}).items():
                if binding.get("variable_id") == identifier or identifier in binding.get("variables", {}).values():
                    bindings.append({"node_id": item["id"], "input": port})
        archive["variables"][identifier] = {
            "type": "string",
            "default": value,
            "value": value,
            "modes": ["keep", "override", "append"],
            "separator": ", ",
            "bindings": bindings,
        }
        archive["interactive_questions"].append(
            {
                "id": "q-" + identifier,
                "variable_id": identifier,
                "prompt": identifier,
                "modes": ["keep", "override", "append"],
            }
        )
    archive["graph"]["edges"] = [
        {
            "id": f"{binding['node_id']}-{item['id']}-{port}",
            "source": binding["node_id"],
            "target": item["id"],
            "kind": "data",
            "source_output": binding["output"],
            "target_input": port,
        }
        for item in archive["graph"]["nodes"]
        for port, binding in item.get("execution", {}).get("inputs", {}).items()
        if binding["kind"] == "node_output"
    ]
    archive["metadata"]["source_mode"] = "mixed"
    archive["metadata"]["attribution"] = "Synthetic acceptance input; not an actual captured creation history"
    archive["metadata"]["external_links"] = [
        "https://github.com/AiCA-Team/AiCA",
        "https://www.bilibili.com/",
        "https://www.youtube.com/",
        "https://huggingface.co/",
    ]
    archive["extensions"]["aica.acceptance"] = {"synthetic": True}
    require_archive(archive)
    return archive


def cases():
    comic = empty_archive("01 / Neon Samurai - image acceptance")
    comic["graph"]["nodes"] = [
        node("story", "text_materialize", {"content": literal("A traveler discovers a signal.")}),
        node("style", "text_materialize", {"content": variable("style")}),
        node("music", "text_materialize", {"content": literal("Background audio reference; no generated soundtrack.")}),
        node(
            "character",
            "image_gen",
            {
                "prompt": template("{{who}} {{extra}}", who="character", extra="detail"),
                "style": variable("style"),
                "brief": output("story"),
            },
        ),
        node(
            "panel_1",
            "image_gen",
            {
                "prompt": template("Close shot of {{who}} {{extra}}", who="character", extra="detail"),
                "style": variable("style"),
                "reference_artifact_ids": output("character"),
            },
        ),
        node(
            "panel_2",
            "image_gen",
            {
                "prompt": template("Wide view of {{who}} {{extra}}", who="character", extra="detail"),
                "style": variable("style"),
                "reference_artifact_ids": output("character"),
            },
        ),
        node(
            "layout",
            "layout_compose",
            {
                "left": output("panel_1"),
                "right": output("panel_2"),
                "title": literal("Image sequence"),
                "audio_note": output("music"),
            },
            params={"layout": {"direction": "vertical", "input_ports": ["left", "right"]}},
        ),
    ]
    finish(comic, {"character": "silver-haired traveler", "style": "dark science fiction", "detail": ""})
    fantasy = empty_archive("02 / Watercolor field guide")
    fantasy["graph"]["nodes"] = [
        node("biome_notes", "text_materialize", {"content": variable("environment")}, "extracted"),
        node("swatches", "text_materialize", {"content": variable("color_palette")}),
        node(
            "wildlife_plate",
            "image_gen",
            {
                "prompt": template("watercolor {{creature}}", creature="creature"),
                "style": variable("color_palette"),
            },
            params={"seed": 410, "layer_role": "subject"},
        ),
        node(
            "habitat_plate",
            "image_gen",
            {
                "prompt": template("watercolor forest landscape {{place}}", place="environment"),
                "style": variable("color_palette"),
                "notes": output("biome_notes"),
            },
            params={"seed": 410, "layer_role": "background"},
        ),
        node(
            "field_guide",
            "layout_compose",
            {
                "animal": output("wildlife_plate"),
                "environment": output("habitat_plate"),
                "swatches": output("swatches"),
            },
            params={
                "layout": {
                    "mode": "layer_stack",
                    "base_input": "environment",
                    "layers": [{"input": "animal", "role": "subject", "rect": [0.25, 0.12, 0.50, 0.84]}],
                }
            },
        ),
    ]
    finish(
        fantasy,
        {"environment": "misty forest", "creature": "small dragon", "color_palette": "watercolor sage and rose"},
    )
    archive = empty_archive("03 / Self archive - extracted development record")
    archive["graph"]["nodes"] = [
        node(
            "capture_note",
            "text_materialize",
            {"content": literal("Synthetic recorded note: define a reusable archive format.")},
            "recorded",
            {"mime_type": "text/markdown"},
        ),
        node("build_configuration", "json_materialize", {"content": variable("framework")}, "extracted"),
        node(
            "review_note",
            "text_materialize",
            {"content": variable("review_note"), "configuration": output("build_configuration")},
            "inferred",
        ),
        node(
            "release_report",
            "layout_compose",
            {
                "record": output("capture_note"),
                "config": output("build_configuration"),
                "review": output("review_note"),
            },
            "inferred",
            {"layout": {"columns": 1, "cell_height": 192}},
        ),
    ]
    rejected = node("rejected_experiment", "image_gen", {"prompt": literal("Rejected experiment")}, "extracted")
    rejected["branch_id"] = "discarded"
    archive["graph"]["nodes"].append(rejected)
    archive["graph"]["branches"].append(
        {
            "id": "discarded",
            "parent_id": "main",
            "state": "dead_end",
            "reason": "Historical example: output did not meet the recorded requirements; excluded from replay.",
        }
    )
    finish(archive, {"framework": "FastAPI", "review_note": "Recommended path requires human review."})
    return {"ai-comic": comic, "field-guide": fantasy, "self-archive": archive}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", choices=["ai-comic", "field-guide", "self-archive"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, archive in cases().items():
        if args.case and name != args.case:
            continue
        (args.output / (name + ".aica.json")).write_text(dumps(archive, pretty=True), encoding="utf-8")


if __name__ == "__main__":
    main()
