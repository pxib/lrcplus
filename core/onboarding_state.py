"""Scalar script state and comparisons; no Python expressions are evaluated."""

import base64
import json
import math
import operator
import re


NAME = r"[A-Za-z_][A-Za-z_0-9]*"


def encode_payload(value):
    return base64.urlsafe_b64encode(json.dumps(value, ensure_ascii=False).encode("utf-8")).decode("ascii")


def decode_payload(value):
    return json.loads(base64.urlsafe_b64decode(value).decode("utf-8"))


def literal(source, location):
    aliases = {"true": True, "false": False, "none": None, "null": None}
    if source.lower() in aliases:
        return aliases[source.lower()]
    try:
        value = json.loads(source)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{location}: expected a string, number, boolean, or null") from exc
    if type(value) not in (str, int, float, bool, type(None)) or (
            isinstance(value, float) and not math.isfinite(value)):
        raise ValueError(f"{location}: expected a finite scalar value")
    return value


def state_command(statement, location):
    assignment = re.fullmatch(rf"({NAME})\s*(=|\+=|-=)\s*(.+)", statement)
    call = re.fullmatch(rf"(set|default|toggle)\(\s*({NAME})\s*(?:,\s*(.+))?\)", statement)
    if assignment:
        name, operation, source = assignment.groups()
        operation = {"=": "set", "+=": "add", "-=": "subtract"}[operation]
    elif call:
        operation, name, source = call.groups()
        if (operation == "toggle") != (source is None):
            raise ValueError(f"{location}: invalid arguments for {operation}")
    else:
        return None
    if name == "event":
        raise ValueError(f"{location}: event is read-only")
    value = literal(source.strip(), location) if source is not None else None
    if operation in ("add", "subtract") and type(value) not in (int, float):
        raise ValueError(f"{location}: counters require a number")
    return {"op": operation, "name": name, "value": value}


def parse_condition(source, location):
    comparison = re.fullmatch(rf"({NAME})\s*(==|!=|>=|<=|>|<)\s*(.+)", source)
    if comparison:
        name, operation, value = comparison.groups()
        return {"name": name, "op": operation, "value": literal(value.strip(), location)}
    flag = re.fullmatch(rf"(not\s+)?({NAME})", source)
    if flag:
        return {"name": flag.group(2), "op": "not" if flag.group(1) else "truthy"}
    raise ValueError(f"{location}: expected a flag or comparison with a literal")


def apply_state(values, command):
    name, operation = command["name"], command["op"]
    value = command.get("value")
    if operation == "set":
        values[name] = value
    elif operation == "default":
        values.setdefault(name, value)
    elif operation == "toggle":
        values[name] = not bool(values.get(name, False))
    elif operation in ("add", "subtract"):
        current = values.get(name, 0)
        if type(current) not in (int, float):
            raise ValueError(f"counter {name!r} is not a number")
        result = current + value if operation == "add" else current - value
        if isinstance(result, float) and not math.isfinite(result):
            raise ValueError(f"counter {name!r} must remain finite")
        values[name] = result


def matches_condition(values, condition, event=None):
    name, operation = condition["name"], condition["op"]
    current = event if name == "event" else values.get(name)
    if operation in ("truthy", "not"):
        return bool(current) if operation == "truthy" else not bool(current)
    expected = condition["value"]
    if name == "event" and isinstance(current, str) and isinstance(expected, str):
        current, expected = current.lower(), expected.lower()
    # Keep booleans distinct from numbers, and avoid ordering unlike types.
    compatible = type(current) is type(expected) or (
        type(current) in (int, float) and type(expected) in (int, float))
    if not compatible:
        return operation == "!="
    compare = {"==": operator.eq, "!=": operator.ne, ">": operator.gt,
               "<": operator.lt, ">=": operator.ge, "<=": operator.le}[operation]
    try:
        return compare(current, expected)
    except TypeError:
        return False
