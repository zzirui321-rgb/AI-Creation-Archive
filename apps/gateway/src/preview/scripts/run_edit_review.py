import argparse

if __name__ == "__main__":
    import uvicorn
    from preview.edit_review_api import create_review_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8102)
    args = parser.parse_args()
    uvicorn.run(create_review_app(args.data_dir), host="127.0.0.1", port=args.port)
