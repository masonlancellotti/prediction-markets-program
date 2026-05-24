from __future__ import annotations

from typing import Any


class SchemaValidationError(ValueError):
    pass


def validate_json_schema_subset(instance: Any, schema: dict[str, Any]) -> None:
    """Validate the JSON Schema subset used by local fixture contracts."""

    root = schema

    def resolve_ref(ref: str) -> dict[str, Any]:
        if not ref.startswith("#/"):
            raise SchemaValidationError(f"unsupported $ref {ref}")
        current: Any = root
        for part in ref[2:].split("/"):
            current = current[part]
        if not isinstance(current, dict):
            raise SchemaValidationError(f"invalid $ref target {ref}")
        return current

    def fail(path: str, message: str) -> None:
        raise SchemaValidationError(f"{path or '$'}: {message}")

    def validate(value: Any, spec: dict[str, Any], path: str) -> None:
        if "$ref" in spec:
            validate(value, resolve_ref(spec["$ref"]), path)
            return

        if "const" in spec and value != spec["const"]:
            fail(path, f"expected const {spec['const']!r}")
        if "enum" in spec and value not in spec["enum"]:
            fail(path, f"expected one of {spec['enum']!r}")

        expected_type = spec.get("type")
        if expected_type is not None and not _matches_type(value, expected_type):
            fail(path, f"expected type {expected_type!r}")

        if isinstance(value, dict):
            properties = spec.get("properties", {})
            required = spec.get("required", [])
            for key in required:
                if key not in value:
                    fail(path, f"missing required property {key!r}")
            if spec.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    fail(path, f"unknown properties {extra!r}")
            for key, nested in value.items():
                if key in properties:
                    validate(nested, properties[key], f"{path}.{key}" if path else key)
                else:
                    additional = spec.get("additionalProperties")
                    if isinstance(additional, dict):
                        validate(nested, additional, f"{path}.{key}" if path else key)

        if isinstance(value, list):
            if "minItems" in spec and len(value) < spec["minItems"]:
                fail(path, f"expected at least {spec['minItems']} items")
            if "maxItems" in spec and len(value) > spec["maxItems"]:
                fail(path, f"expected at most {spec['maxItems']} items")
            if spec.get("uniqueItems") and len({_hashable(item) for item in value}) != len(value):
                fail(path, "expected unique items")
            item_spec = spec.get("items")
            if isinstance(item_spec, dict):
                for index, item in enumerate(value):
                    validate(item, item_spec, f"{path}[{index}]")

        if isinstance(value, str) and "minLength" in spec and len(value) < spec["minLength"]:
            fail(path, f"expected minLength {spec['minLength']}")
        if isinstance(value, int) and not isinstance(value, bool) and "minimum" in spec and value < spec["minimum"]:
            fail(path, f"expected minimum {spec['minimum']}")

    validate(instance, schema, "")


def _matches_type(value: Any, expected_type: str | list[str]) -> bool:
    expected = [expected_type] if isinstance(expected_type, str) else expected_type
    return any(_matches_single_type(value, item) for item in expected)


def _matches_single_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise SchemaValidationError(f"unsupported type {expected_type!r}")


def _hashable(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, _hashable(nested)) for key, nested in value.items()))
    if isinstance(value, list):
        return tuple(_hashable(item) for item in value)
    return value
