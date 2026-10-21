# SPDX-License-Identifier: AGPL-3.0-or-later
"""A small, dependency-free validator for the frozen ci-v1 JSON Schema subset.

The verifier must run without the local v1.5/bwrap validator and without third-party packages, so this module implements
exactly the keywords used by ``ci/schema/ci-v1.json``: type, const, enum, pattern, required, additionalProperties,
properties, items, minItems, maxItems and minimum. Unknown keywords are refused rather than ignored.
"""
from __future__ import annotations

import re

from .canon import CanonError, loads

_KNOWN = {"$schema", "$id", "title", "type", "const", "enum", "pattern", "required", "additionalProperties",
          "properties", "items", "minItems", "maxItems", "minimum", "maximum", "minLength", "maxLength"}


class SchemaError(ValueError):
    """A receipt does not match the frozen schema."""


def _type_ok(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if isinstance(expected, list):
        return any(_type_ok(value, item) for item in expected)
    raise SchemaError(f"unknown schema type {expected!r}")


def _check(value, schema, path):
    unknown = set(schema) - _KNOWN
    if unknown:
        raise SchemaError(f"unsupported schema keyword(s) at {path}: {sorted(unknown)}")
    if "type" in schema and not _type_ok(value, schema["type"]):
        raise SchemaError(f"{path}: expected {schema['type']}")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: must be one of {schema['enum']!r}")
    if "pattern" in schema and isinstance(value, str) and re.fullmatch(schema["pattern"], value) is None:
        raise SchemaError(f"{path}: does not match {schema['pattern']!r}")
    if "minimum" in schema and isinstance(value, int) and not isinstance(value, bool) and value < schema["minimum"]:
        raise SchemaError(f"{path}: below minimum {schema['minimum']}")
    if "maximum" in schema and isinstance(value, int) and not isinstance(value, bool) and value > schema["maximum"]:
        raise SchemaError(f"{path}: above maximum {schema['maximum']}")
    if "minLength" in schema and isinstance(value, str) and len(value) < schema["minLength"]:
        raise SchemaError(f"{path}: shorter than {schema['minLength']}")
    if "maxLength" in schema and isinstance(value, str) and len(value) > schema["maxLength"]:
        raise SchemaError(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise SchemaError(f"{path}: missing required key {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaError(f"{path}: unexpected key(s) {sorted(extra)}")
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], f"{path}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], f"{path}[{index}]")


def validate(receipt, schema):
    _check(receipt, schema, "$")
    return receipt


def load_schema(path):
    with open(path, "rb") as handle:
        return loads(handle.read())


def validate_file(receipt_path, schema_path):
    try:
        with open(receipt_path, "rb") as handle:
            receipt = loads(handle.read())
    except (CanonError, OSError) as exc:
        raise SchemaError(f"receipt is not canonical JSON: {exc}") from None
    return validate(receipt, load_schema(schema_path))
