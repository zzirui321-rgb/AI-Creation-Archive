import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, SchemaError, ValidationError


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    return result


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(root, location):
    path = Path(location)
    require(not path.is_absolute() and ".." not in path.parts and "\\" not in location,
            f"Resource must use a repository-relative location: {location}")
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root) and resolved.exists(), f"Resource is missing or escapes the repository: {location}")
    return resolved


def validate(root):
    manifest = read_json(root / "collaboration.json")
    resources = manifest["resources"]
    schema = read_json(local_path(root, resources["manifest_schema"]))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    for location in resources.values():
        local_path(root, location)
    modules = manifest["modules"]
    bindings = {}
    for binding in manifest["bindings"]:
        resource = binding["resource_id"]
        require(resource not in bindings, f"Duplicate binding: {resource}")
        require(resource in resources, f"Unknown binding resource: {resource}")
        require(binding["module_id"] in modules, f"Unknown module for {resource}")
        locator = binding["locator"]
        if locator is not None:
            require(locator["relative_location"] == resources[resource], f"Resource mapping disagrees: {resource}")
            path = local_path(root, locator["relative_location"])
            require(locator["kind"] != "directory" or path.is_dir(), f"Expected directory: {resource}")
            require(locator["kind"] != "file" or path.is_file(), f"Expected file: {resource}")
        bindings[resource] = binding
    for module_id, module in modules.items():
        require(module["owner_role"] in manifest["roles"], f"Unknown owner role: {module_id}")
        if module["binding_status"] == "accepted":
            require(any(item["module_id"] == module_id and item["status"] == "accepted"
                        for item in bindings.values()), f"Accepted module has no accepted binding: {module_id}")
    commands = manifest["commands"]
    for command_id, command in commands.items():
        require(command["cwd_resource_id"] in resources, f"Unknown command directory: {command_id}")
        require(local_path(root, resources[command["cwd_resource_id"]]).is_dir(),
                f"Command directory is not a directory: {command_id}")
    for check_id, check in manifest["checks"].items():
        require(check["command_ref"] is None or check["command_ref"] in commands, f"Unknown command for {check_id}")
        require(check["status"] != "connected" or check["command_ref"] is not None,
                f"Connected check has no command: {check_id}")
    environment = manifest["environment"]
    for resource in [environment["reference_resource_id"], *environment["protected_resource_ids"]]:
        require(resource in resources, f"Unknown environment resource: {resource}")
    for command in environment["command_refs"].values():
        require(command is None or command in commands, f"Unknown environment command: {command}")
    task_schema = read_json(local_path(root, resources["task_schema"]))
    Draft202012Validator.check_schema(task_schema)
    validator = Draft202012Validator(task_schema, format_checker=FormatChecker())
    tasks = {}
    for path in sorted(local_path(root, resources["tasks"]).glob("*.json")):
        task = read_json(path)
        validator.validate(task)
        task_id = task["task_id"]
        require(task_id not in tasks and path.stem == task_id, f"Duplicate or misplaced task: {task_id}")
        require(task["owner_role"] in manifest["roles"], f"Unknown task owner role: {task_id}")
        require(set(task["scope"]["module_ids"]) <= modules.keys(), f"Unknown task module: {task_id}")
        require(set(task["scope"]["resource_ids"]) <= resources.keys(), f"Unknown task resource: {task_id}")
        require(set(task["required_check_refs"]) <= manifest["checks"].keys(), f"Unknown task check: {task_id}")
        for step in task["next_steps"]:
            require(step["module_id"] in modules, f"Unknown next-step module: {task_id}")
            require(step["resource_id"] is None or step["resource_id"] in resources, f"Unknown next-step resource: {task_id}")
            require(step["command_ref"] is None or step["command_ref"] in commands, f"Unknown next-step command: {task_id}")
        for evidence in task["evidence"]:
            require(evidence["check_ref"] in manifest["checks"], f"Unknown evidence check: {task_id}")
            require(evidence["command_ref"] is None or evidence["command_ref"] in commands,
                    f"Unknown evidence command: {task_id}")
            require(evidence["result"] != "passed" or evidence["command_ref"] is not None,
                    f"Passed evidence lacks a command: {task_id}")
        tasks[task_id] = task
    remaining = {task_id: set(task["depends_on"]) for task_id, task in tasks.items()}
    for task_id, dependencies in remaining.items():
        require(dependencies <= tasks.keys(), f"Unknown dependency: {task_id}")
    while remaining:
        ready = {task_id for task_id, dependencies in remaining.items() if not dependencies}
        require(bool(ready), "Task dependencies contain a cycle")
        remaining = {task_id: dependencies - ready for task_id, dependencies in remaining.items() if task_id not in ready}
    return {"status": "success", "tasks": len(tasks), "bindings": len(bindings),
            "message": "Schema, resource references, command references and task dependency DAG are valid"}


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    try:
        result = validate(root)
    except (OSError, ValueError, KeyError, SchemaError, ValidationError) as error:
        result = {"status": "error", "message": str(error)}
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
