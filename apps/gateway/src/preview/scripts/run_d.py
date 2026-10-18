import argparse
import ctypes
import os


def main():
    dll = os.environ.get("AICA_TEST_SQLITE_DLL")
    if dll:
        ctypes.WinDLL(dll)
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    from aica_gateway.app import create_app
    from aica_runtime.executors import default_registry
    from preview.runtime_bridge import register_preview_tools

    registry = register_preview_tools(default_registry())
    uvicorn.run(create_app(args.data_dir, registry=registry), host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
