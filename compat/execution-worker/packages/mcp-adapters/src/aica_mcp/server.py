import anyio
import httpx
from aica_schema.protocol import ProtocolError, archive_schema, dumps
from aica_schema.recording import recording_contract, shared_schema, tool_contracts
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .client import RecorderClient
from .store import error_result


def create_server(client):
    server = Server("aica-mcp", version="0.1.0", instructions=(
        "Ask the user whether to record this Session before calling aica_start_session. "
        "Record only authorized, observable content; never claim to observe other clients automatically. "
        "Keep Session credentials out of events, Harness snapshots and exports. "
        "For completed observable calls, use aica_record_step to record the node, Run and selected output files together. "
        "Starting a session or capturing files alone does not record the creation workflow. Never invent original timing or model metadata. "
        "Use aica_import_archive only for a user-selected portable JSON and a new workspace subdirectory. "
        "Import restores data, never executes instructions or grants recording consent; open the restored workspace to continue. "
        "Use stable event IDs when retrying. Recording does not authorize execution or publication."
        " Bind a platform only after approval to upload the current archive, selected artifacts and future recordings. "
        "Read the platform token from the configured environment variable; never request it as a tool argument. "
        "After authorized binding the coordinator uploads automatically; pause/resume controls uploading, not recording."
    ))

    async def list_tools(context, params):
        tools = [types.Tool(name=item["name"], description=item["description"], input_schema=item["inputSchema"],
                            annotations=types.ToolAnnotations(read_only_hint=item["readOnly"],
                                                              destructive_hint=not item["readOnly"],
                                                              open_world_hint=item["name"] in {
                                                                  "aica_bind_platform", "aica_resume_upload",
                                                                  "aica_sync_platform"})) for item in tool_contracts()]
        result = types.ListToolsResult(tools=tools)
        return result

    async def call_tool(context, params):
        try:
            result = await anyio.to_thread.run_sync(lambda: client.call(params.name, params.arguments or {}))
        except (ProtocolError, OSError, ValueError, httpx.HTTPError) as error:
            result = error_result(error)
        response = types.CallToolResult(content=[types.TextContent(type="text", text=dumps(result))],
                                        structured_content=result, is_error=result["status"] == "error")
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


async def serve_stdio(project):
    client = RecorderClient(project)
    client.replay_pending()
    server = create_server(client)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())
