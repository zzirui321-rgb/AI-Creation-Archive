import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops


def check(root):
    before, after = [Image.open(root / name).convert("RGBA") for name in ("source.png", "result.png")]
    mask = Image.open(root / "mask.png").convert("L")
    assert before.size == after.size == mask.size
    protected = mask.point(lambda value: 255 if value == 0 else 0)
    changed = Image.new("L", before.size)
    for channel in ImageChops.difference(before, after).split():
        assert ImageChops.multiply(channel, protected).getbbox() is None, "Protected pixels changed"
        changed = ImageChops.lighter(changed, channel)
    assert changed.getbbox(), "No image edit occurred"
    return {
        "protected_pixel_count": protected.histogram()[255],
        "protected_max_rgba_error": 0,
        "changed_pixel_count": before.width * before.height - changed.histogram()[0],
        "source_sha256": hashlib.sha256((root / "source.png").read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256((root / "result.png").read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = check(args.directory)
    (args.directory / "pixels.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
