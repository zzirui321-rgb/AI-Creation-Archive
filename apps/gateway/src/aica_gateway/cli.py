import argparse

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser(description="Serve the AiCA archive API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    arguments = parser.parse_args()
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port, workers=1)
