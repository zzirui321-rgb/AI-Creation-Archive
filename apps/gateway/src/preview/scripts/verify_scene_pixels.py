import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


def check(root):
    def image(phase, name, stage="final"):
        return Image.open(root / f"{phase}-{stage}-{name}.png").convert("RGBA")

    def alpha(phase, name, rect):
        canvas = image(phase, "scene")
        layer = image(phase, name)
        bounds = layer.getchannel("A").getbbox()
        mask = Image.new("L", canvas.size)
        if bounds:
            x, y, width, height = rect
            box = [
                round(x * canvas.width),
                round(y * canvas.height),
                round((x + width) * canvas.width),
                round((y + height) * canvas.height),
            ]
            resized = ImageOps.contain(layer.crop(bounds), (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
            mask.paste(
                resized.getchannel("A"),
                (box[0] + (box[2] - box[0] - resized.width) // 2, box[1] + (box[3] - box[1] - resized.height) // 2),
            )
        return mask

    def unchanged_outside(before, after, support):
        protected = support.point(lambda value: 255 if value == 0 else 0)
        count = protected.histogram()[255]
        assert count > support.width * support.height * 0.45, "Insufficient untouched pixel coverage"
        difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
        for channel in difference.split():
            assert ImageChops.multiply(channel, protected).getbbox() is None, "Protected background pixel changed"
        assert difference.getbbox(), "No actual visual change"
        return count

    subject_rect, detail_rect = [0.27, 0.16, 0.46, 0.80], [0.76, 0.08, 0.20, 0.26]
    old_mask = alpha("original", "subject_layer", subject_rect)
    new_mask = alpha("subject", "subject_layer", subject_rect)
    union = ImageChops.lighter(old_mask, new_mask)
    subject_pixels = unchanged_outside(image("original", "scene"), image("subject", "scene"), union)
    detail_mask = alpha("detail", "detail_layer", detail_rect)
    detail_pixels = unchanged_outside(image("subject", "scene"), image("detail", "scene"), detail_mask)
    assert image("subject", "subject_layer").tobytes() == image("detail", "subject_layer").tobytes()
    assert image("original", "environment_plate").tobytes() == image("subject", "environment_plate").tobytes()
    assert image("subject", "environment_plate").tobytes() == image("detail", "environment_plate").tobytes()
    assert image("detail", "environment_plate").tobytes() != image("background", "environment_plate").tobytes()
    assert image("detail", "subject_layer").tobytes() == image("background", "subject_layer").tobytes()
    assert image("detail", "detail_layer").tobytes() == image("background", "detail_layer").tobytes()
    opaque = ImageChops.lighter(alpha("detail", "subject_layer", subject_rect), detail_mask).point(
        lambda value: 255 if value == 255 else 0
    )
    difference = ImageChops.difference(
        image("detail", "scene").convert("RGB"), image("background", "scene").convert("RGB")
    )
    for channel in difference.split():
        assert ImageChops.multiply(channel, opaque).getbbox() is None, (
            "Opaque reused foreground changed with background"
        )
    union.save(root / "subject-change-support.png")
    detail_mask.save(root / "append-change-support.png")
    return {
        "subject_protected_pixels": subject_pixels,
        "append_protected_pixels": detail_pixels,
        "protected_region_max_error": 0,
        "background_edit_opaque_foreground_error": 0,
        "subject_changed": True,
        "background_changed_independently": True,
        "subject_png_sha256": hashlib.sha256((root / "subject-final-subject_layer.png").read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    result = check(args.directory)
    (args.directory / "pixel-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
