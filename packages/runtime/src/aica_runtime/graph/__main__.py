import argparse
import sys
import zipfile
from pathlib import Path

from aica_mcp.files import private_directory, read_artifact
from aica_mcp.transfer import ARCHIVE_LIMIT, verify_bundle_stream
from aica_schema.protocol import ProtocolError, dumps, loads, new_id, require, require_archive, utc_now

from .assembly import assemble_graph
from .context import prepare_graph
from .provider import GraphProvider, GraphResponseError
from .report import local_image_links, render_input_report, render_report


def source_path(value):
    path = Path(value).resolve()
    if path.is_dir():
        if path.name == ".aica":
            path = path.parent
        path = path / "aica.json"
    require(path.is_file(), "Input must be an A archive JSON, its project directory, or a standard transfer ZIP")
    return path


def load_context(path, import_id, node_ids=None):
    if zipfile.is_zipfile(path):
        with path.open("rb") as stream:
            verified = verify_bundle_stream(stream)
            records = {record["artifact_id"]: record for record in verified["manifest"]["artifacts"]}
            stream.seek(0)
            with zipfile.ZipFile(stream) as bundle:
                def read_history(identifier, limit):
                    record = records[identifier]
                    require(record["size"] <= limit, "History exceeds parsing budget", "analysis_limit")
                    return bundle.read(record["path"])

                return prepare_graph(bundle.read("aica.json"), import_id, read_history, node_ids)
    with path.open("rb") as stream:
        raw = stream.read(ARCHIVE_LIMIT + 1)
    require(len(raw) <= ARCHIVE_LIMIT, "Archive exceeds 16 MiB", "analysis_limit")
    archive = loads(raw.decode("utf-8"))
    require_archive(archive)
    artifacts = {item["id"]: item for item in archive.get("artifacts", [])}

    def read_local(identifier, limit):
        artifact = artifacts[identifier]
        require(artifact.get("size", 0) <= limit, "History exceeds parsing budget", "analysis_limit")
        return read_artifact(path.parent, artifact, limit)

    return prepare_graph(raw, import_id, read_local, node_ids)


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(dumps(value, pretty=True) + "\n")


def preview(input_path, output, node_ids=None):
    path, output = source_path(input_path), Path(output).resolve()
    require(not output.exists(), "Output directory exists; use a new run directory")
    require(not output.is_relative_to(path.parent), "Keep test output outside the source project")
    require(not any((parent / ".git").exists() for parent in [output, *output.parents]),
            "Keep private test output outside Git checkouts")
    import_id = new_id()
    context = load_context(path, import_id, node_ids)
    require(bool(context.prepared["facts"]), "No analysable nodes, messages or registered creative artifacts in this input", "analysis_empty")
    private_directory(output)
    write_json(output / "request.json", {"source_path": str(path), "import_id": import_id, "node_ids": node_ids,
               "input_sha256": context.prepared["input_sha256"], "graph_id": new_id(), "revision_id": new_id(), "branch_id": new_id()})
    write_json(output / "prepared-input.json", context.prepared)
    with (output / "input-report.html").open("x", encoding="utf-8") as stream:
        stream.write(render_input_report(context.prepared, local_image_links(path, context.archive)))
    materials = [fact for fact in context.prepared["facts"] if fact.get("artifact_id")]
    return {"status": "prepared", "model_calls": 0, "output": str(output), "facts": len(context.prepared["facts"]),
            "source_nodes": len(context.prepared["source_node_ids"]), "creative_artifacts": len(materials),
            "document_contents": sum(fact.get("content_state") == "available" for fact in materials),
            "input_report": str(output / "input-report.html"),
            "coverage": context.prepared["coverage"]}


def parse(output, confirm_external, provider=None, max_output_tokens=None):
    require(confirm_external is True, "Review prepared-input.json and use --confirm-external to send it to DeepSeek", "not_authorized")
    output = Path(output).resolve()
    require(not (output / "attempt.json").exists(), "This run already has an attempt; inspect its status instead of retrying")
    request = loads((output / "request.json").read_text(encoding="utf-8"))
    context = load_context(source_path(request["source_path"]), request["import_id"], request["node_ids"])
    require(bool(context.prepared["facts"]), "Empty parsing input cannot be sent to the model", "analysis_empty")
    reviewed = loads((output / "prepared-input.json").read_text(encoding="utf-8"))
    require(context.prepared == reviewed and context.prepared["input_sha256"] == request["input_sha256"],
            "Source or reviewed input changed; create a new preview", "analysis_input_conflict")
    provider = (provider or GraphProvider()).snapshot()
    if max_output_tokens is not None:
        require(type(max_output_tokens) is int and 512 <= max_output_tokens <= 8192,
                "max-output-tokens must be between 512 and 8192", "invalid_output_budget")
        provider.settings = provider.configuration().model_copy(update={"max_tokens": max_output_tokens})
    attempt = {"state": "running", "started_at": utc_now(), "engine": provider.describe(), "automatic_retries": 0}
    require(not any((output / name).exists() for name in ["graph.json", "provider-result.json", "report.html"]),
            "Output artifacts already exist; preserved without a model call")
    write_json(output / "attempt.json", attempt)
    try:
        result = provider.propose(context.prepared)
        write_json(output / "provider-result.json", result)
        graph = assemble_graph(result["proposal"], context, request["graph_id"], request["revision_id"], request["branch_id"], result["model"])
        write_json(output / "graph.json", graph)
        with (output / "report.html").open("x", encoding="utf-8") as stream:
            stream.write(render_report(graph, context.prepared))
        attempt.update(state="succeeded", usage=result["usage"], graph_id=graph["graph_id"], revision_id=graph["revision_id"])
    except BaseException as error:
        if isinstance(error, GraphResponseError):
            write_json(output / "provider-response.json", error.diagnostic)
            attempt.update(error_details=error.details, usage=error.diagnostic.get("usage", {}))
        elif isinstance(error, ProtocolError) and error.details:
            attempt["error_details"] = error.details
        attempt.update(state="unknown" if not isinstance(error, ProtocolError) or error.code == "provider_connection" else "failed",
                       error_code=error.code if isinstance(error, ProtocolError) else "parse_interrupted")
        raise
    finally:
        attempt["updated_at"] = utc_now()
        (output / "attempt.json").write_text(dumps(attempt, pretty=True) + "\n", encoding="utf-8")
    return {**attempt, "graph_file": str(output / "graph.json"), "report_file": str(output / "report.html")}


def main():
    cli = argparse.ArgumentParser(description="Test A archives using the production Graph parser; original files stay unchanged")
    commands = cli.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("preview", help="Validate and save reviewed input; no model request")
    prepare.add_argument("input", help="aica.json / exported JSON / project directory / .aica directory / standard bundle ZIP")
    prepare.add_argument("--output", required=True, type=Path, help="New directory outside source project and Git checkouts")
    prepare.add_argument("--node-id", action="append", help="Select a raw node; repeat to select a subgraph")
    run = commands.add_parser("parse", help="Parse reviewed input once and write graph.json + report.html")
    run.add_argument("output", type=Path)
    run.add_argument("--confirm-external", action="store_true")
    run.add_argument("--max-output-tokens", type=int, help="Explicit output budget (512..8192); default uses provider config")
    args = cli.parse_args()
    try:
        result = preview(args.input, args.output, args.node_id) if args.command == "preview" else parse(
            args.output, args.confirm_external, max_output_tokens=args.max_output_tokens)
        print(dumps(result, pretty=True))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(dumps({"status": "error", "code": getattr(error, "code", "local_input_error"),
                     "details": error.details if isinstance(error, ProtocolError) else [],
                     "message": str(error) if isinstance(error, ProtocolError) else "Input/output could not be read; inspect local paths"}), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
