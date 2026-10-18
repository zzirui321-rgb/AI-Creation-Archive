import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
from aica_schema.protocol import ProtocolError, digest, dumps, loads, validate_archive

from .capture import run_command
from .client import RecorderClient, RecordingSource
from .consent import control_request, open_connection
from .coordinator import Coordinator
from .extraction import extract_git, extract_log, extract_steps
from .hosts import CLIENTS, config_bundle, hook_config, run_hook
from .server import serve_stdio
from .store import error_result
from .transfer import build_bundle, upload_bundle, verify_bundle


def parser():
    root = argparse.ArgumentParser(prog="aica", description="AiCA v0.1 recorder and MCP server")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("serve", "coordinator", "status", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=name in {"serve", "coordinator"}, default=".")
        if name == "serve":
            command.add_argument("--per-turn", "--codex-turns", dest="per_turn", action="store_true", help="Require Host-triggered per-turn confirmation")
            command.add_argument("--client", choices=CLIENTS, default="codex")
            command.add_argument("--default-yes", action="store_true", help="Allow native per-turn default Yes without browser fallback")
    commands.add_parser("codex-hook", help="Authenticated local Codex Hook handler; reads one envelope from stdin")
    commands.add_parser("codex-config", help="Print reviewable hooks.json for the current Python environment; never installs or trusts it")
    sync = commands.add_parser("sync", help="Inspect or control automatic bidirectional synchronization")
    sync.add_argument("action", choices=("status", "pause", "resume", "resolve"), default="status", nargs="?")
    sync.add_argument("--project", default=".")
    sync.add_argument("--prefer", choices=("local", "remote"))
    sync.add_argument("--conflict-id")
    for name in ("connect", "pause", "resume", "disconnect"):
        command = commands.add_parser(name, help="Manage browser-approved project recording and uploading")
        command.add_argument("--project", default=".")
        if name == "connect":
            command.add_argument("--server", help="Platform origin; omit to reconnect the saved destination")
            command.add_argument("--allow-loopback-http", action="store_true", help="Allow private-network HTTP for development")
    agent_hook = commands.add_parser("agent-hook", help="Read one native Hook envelope from stdin")
    agent_hook.add_argument("--client", choices=CLIENTS, required=True)
    agent_hook.add_argument("--event")
    agent_hook.add_argument("--confirmation", choices=("auto", "native", "browser", "native-default-yes", "conversation"), default="auto")
    agent_config = commands.add_parser("agent-config", help="Generate client MCP and Hook configuration for review")
    agent_config.add_argument("--client", choices=CLIENTS, required=True)
    agent_config.add_argument("--confirmation", choices=("auto", "native", "browser", "native-default-yes", "conversation"), default="auto")
    agent_config.add_argument("--surface", choices=("cli", "vscode"), default="cli")
    agent_config.add_argument("--output", help="New directory for generated files; existing paths are never overwritten")
    export = commands.add_parser("export", help="Export the current archive or one self-contained JSON")
    export.add_argument("--project", required=True)
    export.add_argument("--portable", action="store_true", help="Embed all registered artifact bytes for platform import")
    export.add_argument("--output", help="Project-relative JSON path; defaults to exports/aica.json with --portable")
    restore = commands.add_parser("import", help="Restore one self-contained JSON into a new project subdirectory")
    restore.add_argument("--project", required=True)
    restore.add_argument("--path", required=True, help="Project-relative source JSON")
    restore.add_argument("--target", required=True, help="New project-relative directory; its parent must exist")
    restore.add_argument("--request-id", help="Stable request ID; defaults to one derived from source and target paths")
    restore.add_argument("--confirm-import", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("path")
    capture = commands.add_parser("run", help="Capture one explicitly requested local command")
    capture.add_argument("--project", required=True)
    capture.add_argument("--record", choices=("yes", "no"), required=True)
    capture.add_argument("argv", nargs=argparse.REMAINDER)
    extract = commands.add_parser("extract")
    extract.add_argument("--project", required=True)
    extract.add_argument("--record", choices=("yes", "no"), required=True)
    extract.add_argument("--format", choices=("git", "codex", "claude_code", "normalized", "steps"), required=True)
    extract.add_argument("--path")
    extract.add_argument("--limit", type=int, choices=range(1, 501), default=50, metavar="1..500")
    replay = commands.add_parser("retry", help="Resend a durable source outbox after reconnection")
    replay.add_argument("--project", required=True)
    replay.add_argument("--source-id", required=True)
    replay.add_argument("--session-id", required=True)
    replay.add_argument("--token-file", required=True)
    bundle = commands.add_parser("bundle", help="Package a snapshot and all artifact bytes for remote import")
    bundle.add_argument("--project", required=True)
    bundle.add_argument("--output", required=True)
    bundle.add_argument("--offline", action="store_true", help="Use existing aica.json without starting a recorder")
    inspect = commands.add_parser("verify-bundle", help="Verify a transfer bundle without extracting it")
    inspect.add_argument("path")
    upload = commands.add_parser("upload", help="Upload a reviewed bundle to a platform import endpoint")
    upload.add_argument("path")
    upload.add_argument("--url", required=True, help="Exact HTTPS import URL supplied by the backend")
    upload.add_argument("--token-env", default="AICA_UPLOAD_TOKEN")
    upload.add_argument("--confirm-upload", action="store_true")
    platform = commands.add_parser("platform", help="Bind, pause and inspect automatic platform uploads")
    actions = platform.add_subparsers(dest="platform_command", required=True)
    for name in ("bind", "status", "pause", "resume", "sync"):
        action = actions.add_parser(name)
        action.add_argument("--project", default=".")
        if name in {"bind", "resume"}:
            action.add_argument("--confirm-upload", action="store_true")
        if name == "bind":
            action.add_argument("--server", required=True, help="HTTPS platform origin")
            action.add_argument("--project-id", required=True, help="Existing platform project UUID")
            action.add_argument("--token-env", default="AICA_UPLOAD_TOKEN")
            action.add_argument("--interval", type=int, default=5)
            action.add_argument("--allow-loopback-http", action="store_true", help="Allow plain HTTP to loopback and private-network hosts (dev/testing only)")
    return root


def main():
    arguments = parser().parse_args()
    try:
        if arguments.command == "serve":
            asyncio.run(serve_stdio(arguments.project, arguments.per_turn, arguments.client, arguments.default_yes))
            return
        if arguments.command == "codex-hook":
            print(dumps(run_hook()))
            return
        if arguments.command == "codex-config":
            print(dumps(hook_config(), pretty=True))
            return
        if arguments.command == "agent-hook":
            print(dumps(run_hook(arguments.client, arguments.event, arguments.confirmation)))
            return
        if arguments.command == "agent-config":
            print(dumps(config_bundle(arguments.client, arguments.confirmation, arguments.surface, arguments.output), pretty=True))
            return
        if arguments.command == "coordinator":
            coordinator = Coordinator(arguments.project)
            coordinator.serve()
            return
        if arguments.command == "sync":
            client = RecorderClient(arguments.project)
            options = {"action": arguments.action}
            if arguments.prefer:
                options["choice"] = arguments.prefer
            if arguments.conflict_id:
                options["conflict_id"] = arguments.conflict_id
            result = control_request(client.project, "sync_control", options)
        elif arguments.command in {"connect", "pause", "resume", "disconnect"}:
            client = RecorderClient(arguments.project)
            status = client.request("status")
            if status.get("project_connection_version") != 1:
                raise ProtocolError("Stop this project coordinator and reconnect MCP with the current code before connecting",
                                    "coordinator_upgrade_required")
            if arguments.command == "connect":
                options = {"allow_loopback_http": arguments.allow_loopback_http}
                if arguments.server:
                    options["server_url"] = arguments.server
                result = open_connection(client.project, **options)
            else:
                result = control_request(client.project, "project_access", {"action": arguments.command})
        elif arguments.command == "validate":
            result = validate_archive(loads(Path(arguments.path).read_text(encoding="utf-8")))
        elif arguments.command == "import":
            if not arguments.confirm_import:
                raise ProtocolError("Local import requires --confirm-import", "not_authorized")
            client = RecorderClient(arguments.project, autostart=False)
            request_id = arguments.request_id or "import-" + digest([arguments.path, arguments.target])
            result = client.call("aica_import_archive", {"request_id": request_id, "path": arguments.path,
                                 "target": arguments.target, "confirm_import": True})
        elif arguments.command == "bundle":
            expected = None
            if not arguments.offline:
                client = RecorderClient(arguments.project)
                exported = client.call("aica_export_archive")
                if exported["status"] != "success":
                    raise ProtocolError(exported.get("message", "Export failed"), exported.get("code", "export_failed"))
                expected = exported["sha256"]
            result = build_bundle(arguments.project, arguments.output, expected)
        elif arguments.command == "verify-bundle":
            result = verify_bundle(arguments.path)
        elif arguments.command == "upload":
            result = upload_bundle(arguments.path, arguments.url, os.environ.get(arguments.token_env),
                                   arguments.confirm_upload)
        elif arguments.command == "platform":
            client = RecorderClient(arguments.project)
            methods = {"bind": "aica_bind_platform", "status": "aica_platform_status", "pause": "aica_pause_upload",
                       "resume": "aica_resume_upload", "sync": "aica_sync_platform"}
            parameters = {}
            if arguments.platform_command in {"bind", "resume"}:
                if not arguments.confirm_upload:
                    raise ProtocolError("Platform upload requires --confirm-upload", "upload_not_authorized")
                parameters["confirm_upload"] = True
            if arguments.platform_command == "bind":
                parameters.update({"server_url": arguments.server, "project_id": arguments.project_id,
                                   "token_env": arguments.token_env, "interval_seconds": arguments.interval,
                                   "allow_loopback_http": arguments.allow_loopback_http})
            result = client.call(methods[arguments.platform_command], parameters)
        else:
            client = RecorderClient(arguments.project, autostart=arguments.command != "stop")
            if arguments.command == "run":
                argv = arguments.argv[1:] if arguments.argv[:1] == ["--"] else arguments.argv
                result = run_command(client, argv, arguments.record == "yes")
            elif arguments.command == "extract":
                if arguments.format == "git":
                    result = extract_git(client, arguments.record == "yes", arguments.limit)
                elif arguments.format == "steps":
                    if not arguments.path:
                        raise ProtocolError("Step extraction requires a project-relative --path")
                    result = extract_steps(client, arguments.path, arguments.record == "yes")
                else:
                    if not arguments.path:
                        raise ProtocolError("Log extraction requires --path")
                    result = extract_log(client, arguments.path, arguments.format, arguments.record == "yes")
            elif arguments.command == "retry":
                token = Path(arguments.token_file).read_text(encoding="utf-8").strip()
                source = RecordingSource(client, arguments.session_id, token, arguments.source_id)
                try:
                    receipts = source.replay()
                    result = {"status": "success" if all(item["status"] == "success" for item in receipts) else "error",
                              "receipts": receipts}
                finally:
                    source.close()
            else:
                method = {"export": "aica_export_archive", "status": "status", "stop": "shutdown"}[arguments.command]
                options = {"portable": arguments.portable, "output": arguments.output} if arguments.command == "export" else {}
                options = {key: value for key, value in options.items() if value is not None}
                result = client.request(method, options)
        print(dumps(result, pretty=True))
        if result["status"] == "error":
            sys.exit(1)
        if arguments.command == "run" and result.get("exit_code"):
            sys.exit(1)
    except (ProtocolError, OSError, ValueError, httpx.HTTPError) as error:
        print(dumps(error_result(error)), file=sys.stderr)
        sys.exit(1)
