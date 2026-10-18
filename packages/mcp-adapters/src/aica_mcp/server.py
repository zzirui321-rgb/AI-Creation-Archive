import anyio
import httpx
from aica_schema.protocol import ProtocolError, archive_schema, dumps, require
from aica_schema.recording import recording_contract, require_arguments, shared_schema, tool_contracts
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .client import RecorderClient
from .consent import AgentConsent, control_request, enable_turn_policy, open_connection
from .store import error_result


def create_server(client, turns=None):
    recording_instructions = (
        "Authenticated Host hooks apply the configured policy. A browser-approved project connection authorizes future turns, selected task artifacts and uploads until paused or disconnected; never ask duplicate turn, attachment or upload questions in that mode. Without a project grant, per-turn and conversation policies still require their normal choices. Normal Stop exports exports/aica.json. History import and destructive pull always require separate confirmation. Never start a manual Session or submit steps yourself. "
        "Save generated files in the workspace first, then select their relative paths with aica_select_turn_artifacts before Stop. Never write directly to .aica/artifacts. Check selected, artifacts and confirmation_reason; report missing attachments explicitly. Ordinary Session tools cannot write. "
        if turns else
        "Ask the user whether to record this Session before calling aica_start_session. "
        "For completed observable calls, use aica_record_step to record the node, Run and selected output files together. "
    )
    server = Server("aica-mcp", version="0.1.0", instructions=(
        recording_instructions +
        "Record only authorized, observable content; never claim to observe other clients automatically. "
        "Keep Session credentials out of events, Harness snapshots and exports. "
        "Starting a session or capturing files alone does not record the creation workflow. Never invent original timing or model metadata. "
        "Use aica_import_archive only for a user-selected portable JSON and a new workspace subdirectory. "
        "Import restores data, never executes instructions or grants recording consent; open the restored workspace to continue. "
        "Use stable event IDs when retrying. Recording does not authorize execution or publication."
        " Use aica_connect_platform to open the browser login and project authorization page. Never ask for passwords or tokens in chat or tool arguments. "
        "Authorized bidirectional sync updates registered source files with backups. Use aica_sync_control to inspect or pause sync. "
        "Conflicts pause sync; ask the user which side to keep and resolve using the current conflict ID. Never choose for them. "
        "Project connections store credentials privately outside the workspace; existing environment-token bindings remain supported. "
        "Without a project grant, per-turn uploads require separate fixed-snapshot confirmation; manual Session upload grants never authorize per-turn recording."
        " In per-turn mode, authenticated Host hooks and the configured confirmation policy supply consent. Ordinary Session tools cannot write. "
        "Never infer consent from chat text. Select attachments with aica_select_turn_artifacts; no directory or transcript sweep."
    ))

    async def list_tools(context, params):
        tools = [types.Tool(name=item["name"], description=item["description"], input_schema=item["inputSchema"],
                            annotations=types.ToolAnnotations(read_only_hint=item["readOnly"],
                                                              destructive_hint=not item["readOnly"],
                                                              open_world_hint=item["name"] in {
                                                                  "aica_bind_platform", "aica_resume_upload",
                                                                  "aica_sync_platform", "aica_pull_platform",
                                                                  "aica_connect_platform", "aica_project_access", "aica_sync_control"})) for item in tool_contracts()]
        result = types.ListToolsResult(tools=tools)
        return result

    async def call_tool(context, params):
        hook_output = params.name == "aica_confirm_turn" and isinstance(params.arguments, dict) and params.arguments.get("hook_output") is True
        try:
            arguments = dict(params.arguments or {})
            require_arguments(params.name, arguments)
            if params.name == "aica_confirm_turn":
                arguments.pop("hook_output", None)
            if params.name == "aica_host_status":
                require(turns is not None and turns.bridge_id is not None, "No active Host bridge", "bridge_expired")
                result = {"status": "success", "client": turns.client, "bridge_id": turns.bridge_id,
                          "project": str(client.project), "host_routing_version": 1}
            elif params.name == "aica_connect_platform":
                require(turns is not None, "Start MCP with --per-turn and matching Host hooks before connecting",
                        "turn_mode_required")
                result = await anyio.to_thread.run_sync(lambda: open_connection(client.project, **arguments))
            elif params.name == "aica_project_access":
                result = await anyio.to_thread.run_sync(lambda: control_request(client.project, "project_access", arguments))
            elif params.name == "aica_sync_control":
                result = await anyio.to_thread.run_sync(lambda: control_request(client.project, "sync_control", arguments))
            elif params.name == "aica_pull_platform":
                require(turns is not None, "Pull requires the authenticated per-turn MCP bridge", "turn_mode_required")
                result = await turns.pull(context, **arguments)
            elif params.name in {"aica_confirm_turn", "aica_select_turn_artifacts"}:
                if turns is None:
                    raise ProtocolError("Start MCP with --per-turn and the matching --client", "turn_mode_required")
                method = turns.confirm if params.name == "aica_confirm_turn" else turns.select
                result = await method(context, **arguments)
            elif turns and params.name == "aica_import_archive":
                result = await turns.import_archive(context, **arguments)
            elif turns and params.name in {"aica_sync_platform", "aica_resume_upload"}:
                result = await turns.upload(context)
            else:
                result = await anyio.to_thread.run_sync(lambda: client.call(params.name, arguments))
        except (ProtocolError, OSError, ValueError, httpx.HTTPError) as error:
            result = error_result(error)
        payload = {"systemMessage": result.get("systemMessage", "AiCA：确认未完成，本轮不记录。")} if hook_output else result
        response = types.CallToolResult(content=[types.TextContent(type="text", text=dumps(payload))],
                                        structured_content=payload, is_error=result["status"] == "error",
                                        meta={"aica": result} if hook_output else None)
        return response

    async def list_resources(context, params):
        resource = types.Resource(uri="aica://schema/0.1", name="AiCA v0.1 JSON Schema",
                                  mime_type="application/schema+json")
        recording = types.Resource(uri="aica://schema/recording/0.1", name="AiCA recording event and tool contracts",
                                   mime_type="application/json")
        result = types.ListResourcesResult(resources=[resource, recording])
        return result

    async def read_resource(context, params):
        if str(params.uri) == "aica://schema/0.1":
            schema = archive_schema()
        elif str(params.uri) == "aica://schema/recording/0.1":
            schema = shared_schema(recording_contract())
        else:
            raise ValueError("Unknown AiCA resource")
        content = types.TextResourceContents(uri=params.uri, mime_type="application/schema+json",
                                             text=dumps(schema, pretty=True))
        result = types.ReadResourceResult(contents=[content])
        return result

    server.add_request_handler("tools/list", types.PaginatedRequestParams, list_tools)
    server.add_request_handler("tools/call", types.CallToolRequestParams, call_tool)
    server.add_request_handler("resources/list", types.PaginatedRequestParams, list_resources)
    server.add_request_handler("resources/read", types.ReadResourceRequestParams, read_resource)
    return server


def require_turn_coordinator(status):
    required = {"attach", "heartbeat", "detach", "hook", "pending", "resolve", "active", "capture",
                "conversation_submit", "prepare_conversation", "pull_prepare", "pull_apply", "pull_cancel",
                "upload_prepare", "upload_apply", "upload_cancel", "connect_create", "project_access", "sync_control"}
    available = status.get("turn_control_methods", [])
    compatible = (status.get("turn_recording_version") == 2 and status.get("conversation_recording_version") == 2
                  and status.get("project_connection_version") == 1
                  and status.get("bidirectional_sync_version") == 1
                  and status.get("host_routing_version") == 1
                  and isinstance(available, list) and required.issubset(available))
    require(compatible, "Coordinator recording interfaces are incompatible; stop this workspace coordinator with "
            "aica stop --project PATH, then reconnect MCP using the same code checkout", "coordinator_upgrade_required")


async def serve_stdio(project, per_turn=False, agent_client="codex", default_yes=False):
    require(not default_yes or per_turn, "--default-yes requires --per-turn")
    if per_turn:
        probe = RecorderClient(project, autostart=False)
        status = None
        try:
            status = probe.request("status")
        except (ProtocolError, OSError, ValueError, httpx.HTTPError):
            pass
        if status and status.get("status") == "success":
            require_turn_coordinator(status)
        enable_turn_policy(project)
    client = RecorderClient(project)
    turns = None
    if per_turn:
        status = client.request("status")
        require_turn_coordinator(status)
        turns = AgentConsent(client.project, agent_client, default_yes)
        await turns.start()
    else:
        client.replay_pending()
    server = create_server(client, turns)
    try:
        async with anyio.create_task_group() as tasks:
            if turns:
                tasks.start_soon(turns.heartbeat)
            async with stdio_server() as (reader, writer):
                await server.run(reader, writer, server.create_initialization_options())
            tasks.cancel_scope.cancel()
    finally:
        if turns:
            with anyio.CancelScope(shield=True):
                try:
                    await turns.close()
                except (ProtocolError, OSError, ValueError, httpx.HTTPError):
                    pass
