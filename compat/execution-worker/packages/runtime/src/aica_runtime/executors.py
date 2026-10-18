import copy

from aica_schema.protocol import ProtocolError, require

from .graph import active_nodes, ordered_nodes


class ExecutorRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, name, version, handler, cacheable=False, runtime_ref="aica-python@0.1", kind="mcp"):
        require(kind in {"mcp", "model"} and isinstance(version, str) and bool(version),
                "Only versioned registered tools and model adapters are supported")
        require(name not in self.tools, "Executor is already registered")
        self.tools[name] = {"version": version, "handler": handler, "cacheable": cacheable,
                            "runtime_ref": runtime_ref, "kind": kind}

    def resolve(self, execution):
        require(execution.get("kind") in {"mcp", "model"},
                "Script, command and manual execution are unavailable", "executor_unavailable")
        tool = self.tools.get(execution["tool"])
        require(tool is not None and tool["kind"] == execution["kind"],
                "Tool has no registered executor", "executor_unavailable")
        require(execution["version"] in {None, tool["version"]}
                and execution.get("runtime_ref", tool["runtime_ref"]) == tool["runtime_ref"],
                "Registered executor version or environment does not match", "executor_unavailable")
        return tool

    def describe(self):
        result = [{"tool": name, **{key: value for key, value in tool.items() if key != "handler"}}
                  for name, tool in sorted(self.tools.items())]
        return result


def echo(inputs, params, context):
    result = copy.deepcopy(inputs)
    return result


def text_artifact(inputs, params, context):
    require(isinstance(inputs.get("text"), str), "text input must be a string")
    artifact = context.capture(inputs["text"].encode("utf-8"), "text/plain; charset=utf-8")
    return {"text": artifact["id"]}


def default_registry():
    registry = ExecutorRegistry()
    registry.register("aica.echo", "1", echo, cacheable=True)
    registry.register("aica.text-artifact", "1", text_artifact, cacheable=True)
    return registry


def execution_plan(archive, registry, targets=None):
    nodes = active_nodes(archive)
    require(len(nodes) <= 1000, "Runtime MVP supports at most 1000 active nodes", "execution_limit")
    order = ordered_nodes(archive, targets)
    blockers = []
    for node_id in order:
        try:
            require("execution" in nodes[node_id], "Node has no executable definition", "executor_unavailable")
            registry.resolve(nodes[node_id]["execution"])
            timeout = nodes[node_id]["execution"].get("limits", {}).get("timeout_seconds", 60)
            require(type(timeout) in {int, float} and 0 < timeout <= 300,
                    "Executor timeout must be within 300 seconds", "invalid_limits")
        except ProtocolError as error:
            blockers.append({"node_id": node_id, "code": error.code, "message": str(error)})
    if not archive["snapshot"]["complete"]:
        blockers.append({"code": "incomplete_archive", "message": "Recording evidence has unresolved gaps"})
    if not order:
        blockers.append({"code": "empty_execution", "message": "Select at least one executable node"})
    return {"status": "success", "node_ids": order, "blockers": blockers, "executable": not blockers}
