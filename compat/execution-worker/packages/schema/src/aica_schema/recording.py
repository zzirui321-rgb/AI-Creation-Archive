from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from .protocol import ProtocolError, archive_schema, dumps, loads, require_shape


def recording_contract():
    contract = loads(files("aica_schema").joinpath("recording.schema.json").read_text(encoding="utf-8"))
    return contract


def shared_schema(schema):
    result = dict(schema)
    definitions = archive_schema()["$defs"]
    selected = {}

    def visit(value):
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/$defs/"):
                name = reference.split("/")[2]
                if name not in selected:
                    selected[name] = definitions[name]
                    visit(definitions[name])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(schema)
    if selected:
        result["$defs"] = selected
    return result


def tool_contracts():
    tools = recording_contract()["tools"]
    for tool in tools:
        tool["inputSchema"] = shared_schema(tool["inputSchema"])
    return tools


def require_arguments(name, arguments):
    contracts = {tool["name"]: tool for tool in tool_contracts()}
    if name not in contracts:
        raise ProtocolError(f"Unknown tool: {name}", "unknown_tool")
    dumps(arguments)
    errors = list(Draft202012Validator(contracts[name]["inputSchema"], format_checker=FormatChecker())
                  .iter_errors(arguments))
    if errors:
        raise ProtocolError(errors[0].message)


def require_event(event):
    require_shape(event, "event")
    payloads = recording_contract()["payloads"]
    if event["type"] not in payloads:
        raise ProtocolError("This event type is reserved for the recorder")
    schema = shared_schema(payloads[event["type"]])
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(event["payload"]))
    if errors:
        raise ProtocolError(f"Invalid {event['type']} payload: {errors[0].message}")


def require_step_manifest(manifest):
    dumps(manifest)
    schema = shared_schema(recording_contract()["step_manifest"])
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest))
    if errors:
        raise ProtocolError(f"Invalid step manifest: {errors[0].message}", "invalid_step_manifest")


def require_platform_config(config):
    schema = recording_contract()["platform_config"]
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(config))
    if errors:
        raise ProtocolError("Invalid .aicaconfig; use the platform binding tool to configure this project", "invalid_config")
