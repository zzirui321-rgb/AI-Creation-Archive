import argparse

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(prog="aica-gateway")
    parser.add_argument("--data-dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    app = create_app(arguments.data_dir)
    uvicorn.run(app, host=arguments.host, port=arguments.port, workers=1)


if __name__ == "__main__":
    main()
