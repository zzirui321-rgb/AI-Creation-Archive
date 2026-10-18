"""Synthetic independent-layer workflows; no business data enters the engine."""

import argparse
from pathlib import Path

from aica_schema.protocol import dumps, empty_archive

from .generic_cases import finish, literal, node, output, variable


def scene_case():
    archive = empty_archive("Independent scene layers / synthetic acceptance")
    archive["graph"]["nodes"] = [
        node(
            "environment_plate",
            "image_gen",
            {"prompt": variable("environment"), "style": variable("style")},
            params={"seed": 410, "layer_role": "background"},
        ),
        node(
            "subject_layer",
            "image_gen",
            {"prompt": variable("subject"), "style": variable("style")},
            params={"seed": 71, "layer_role": "subject"},
        ),
        node(
            "detail_layer",
            "image_gen",
            {"prompt": variable("detail"), "style": variable("style")},
            params={"seed": 109, "layer_role": "detail"},
        ),
        node(
            "scene",
            "layout_compose",
            {
                "background": output("environment_plate"),
                "subject": output("subject_layer"),
                "detail": output("detail_layer"),
            },
            params={
                "layout": {
                    "mode": "layer_stack",
                    "base_input": "background",
                    "layers": [
                        {"input": "subject", "role": "subject", "rect": [0.27, 0.16, 0.46, 0.80]},
                        {"input": "detail", "role": "detail", "rect": [0.76, 0.08, 0.20, 0.26]},
                    ],
                }
            },
        ),
    ]
    return finish(
        archive,
        {
            "environment": "watercolor quiet forest and distant mountains, empty meadow",
            "style": "delicate watercolor illustration",
            "subject": "a silver-haired traveler wearing a long blue coat, full body",
            "detail": "",
        },
    )


def comic_case():
    archive = scene_case()
    archive["metadata"]["title"] = "Layered panels to comic / synthetic acceptance"
    scene = next(item for item in archive["graph"]["nodes"] if item["id"] == "scene")
    scene["id"] = "panel_one"
    import copy

    other = copy.deepcopy(scene)
    other["id"] = "panel_two"
    other["execution"]["params"]["layout"]["layers"][0]["rect"] = [0.12, 0.22, 0.40, 0.74]
    archive["graph"]["nodes"] += [
        other,
        node(
            "comic",
            "layout_compose",
            {"left": output("panel_one"), "right": output("panel_two"), "title": literal("Layered scene sequence")},
            params={"layout": {"direction": "vertical", "input_ports": ["left", "right"]}},
        ),
    ]
    values = {key: value["value"] for key, value in archive["variables"].items()}
    archive["variables"] = {}
    archive["interactive_questions"] = []
    return finish(archive, values)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, archive in {"layered-scene": scene_case(), "layered-comic": comic_case()}.items():
        (args.output / (name + ".aica.json")).write_text(dumps(archive, pretty=True), encoding="utf-8")
