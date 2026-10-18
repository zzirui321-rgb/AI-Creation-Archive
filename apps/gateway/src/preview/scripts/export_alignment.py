"""Generate separate owner patches without staging or committing."""

import difflib
import io
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[5]
    output = root / "packages/artifact-renderers/handoff/patches"
    output.mkdir(parents=True, exist_ok=True)
    groups = {
        "b": ("5d7c9a2", ["apps/gateway/src/preview", "packages/artifact-renderers"]),
        "c": ("baa6908", ["apps/web"]),
        "d": ("6b98aa4", ["apps/gateway/src/aica_gateway/app.py", "packages/runtime/src/aica_runtime/engine.py"]),
        "examples": ("6b98aa4", ["examples/ai-comic", "examples/workflows", "examples/self-archive"]),
    }
    for name, (base, paths) in groups.items():
        tracked = set(
            subprocess.check_output(["git", "ls-tree", "-r", "--name-only", base, "--", *paths], cwd=root)
            .decode()
            .splitlines()
        )
        extra = (
            subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *paths], cwd=root)
            .decode()
            .split("\0")
        )
        current = subprocess.check_output(["git", "ls-files", "-z", "--", *paths], cwd=root).decode().split("\0")
        patch = ""
        for path in sorted(tracked | set(extra) | set(current)):
            if not path or "/handoff/patches/" in path:
                continue
            before = subprocess.check_output(["git", "show", f"{base}:{path}"], cwd=root) if path in tracked else b""
            target = root / path
            after = target.read_bytes() if target.is_file() else b""
            if before == after:
                continue
            old = before.decode("utf-8").replace("\r\n", "\n")
            new = after.decode("utf-8").replace("\r\n", "\n")
            if old == new:
                continue
            patch += f"diff --git a/{path} b/{path}\n"
            if path not in tracked:
                patch += "new file mode 100644\n"
            elif not target.is_file():
                patch += "deleted file mode 100644\n"
            for line in difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="a/" + path if path in tracked else "/dev/null",
                tofile="b/" + path if target.is_file() else "/dev/null",
            ):
                patch += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        (output / f"{name}.patch").write_text(patch, encoding="utf-8", newline="\n")
        with tempfile.TemporaryDirectory(prefix="aica-owner-patch-") as temporary:
            if tracked:
                content = subprocess.check_output(["git", "archive", "--format=zip", base, *sorted(tracked)], cwd=root)
                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    archive.extractall(temporary)
            subprocess.run(["git", "init", "--quiet", temporary], check=True, capture_output=True)
            verified = subprocess.run(
                ["git", "apply", "--check", str(output / f"{name}.patch")], cwd=temporary, capture_output=True
            )
            if verified.returncode:
                raise RuntimeError(verified.stderr.decode())
        print(name, (output / f"{name}.patch").stat().st_size, "bytes; apply check passed")


if __name__ == "__main__":
    main()
