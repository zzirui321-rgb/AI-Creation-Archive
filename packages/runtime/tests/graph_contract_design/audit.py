import json
from pathlib import Path

from aica_runtime.deepseek import DeepSeekProvider


def main():
    package = Path(__file__).resolve().parents[2]
    checkout = package.parents[1]
    roots = [package / "design" / "graph-v0.1", Path(__file__).resolve().parent]
    paths = [item for root in roots for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts]
    paths += [checkout / "docs" / "STATUS.md", checkout / "tasks" / "D-GRAPH-DESIGN-20261017.json"]
    credential = DeepSeekProvider().configuration().api_key.get_secret_value().encode()
    matches = sum(credential in path.read_bytes() for path in paths)
    print(json.dumps({"files_checked": len(paths), "credential_matches": matches}))
    if matches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
