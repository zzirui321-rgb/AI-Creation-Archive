from importlib.resources import files

from jsonschema import Draft202012Validator

from .protocol import ProtocolError, dumps, loads


def transfer_schema(section="manifest"):
    schema = loads(files("aica_schema").joinpath("transfer.schema.json").read_text(encoding="utf-8"))
    if section not in {"manifest", "receipt"}:
        raise ProtocolError("Unknown transfer contract section")
    schema["$ref"] = f"#/$defs/{section}"
    return schema


def require_transfer(value, section="manifest"):
    dumps(value)
    errors = list(Draft202012Validator(transfer_schema(section)).iter_errors(value))
    if errors:
        raise ProtocolError(f"Invalid transfer {section}: {errors[0].message}", "invalid_transfer")
