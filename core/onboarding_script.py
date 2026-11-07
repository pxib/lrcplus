"""Compile the supported Ren'Py-inspired onboarding script to dialogue cues.

This is a deliberately small, data-only language, not a Python or Ren'Py runtime.
"""

import json
import base64
import re
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "assets" / "onboarding" / "onboarding.rpy"


def _quoted(value, location):
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{location}: expected a quoted string") from exc
    if not isinstance(result, str):
        raise ValueError(f"{location}: expected a quoted string")
    return result


def _cue(statement, location):
    fixed = {
        "next": r"\n", "end": r"\e", "continue_setup": r"\c",
        "hide_cursor": r"\curhide", "toggle_outside_click": r"\toggleoutsideclick",
        "reset": r"\res",
    }
    if statement in fixed:
        return fixed[statement]
    return_marker = re.fullmatch(r"return\s+checkpoint\((\d+)\)", statement)
    if return_marker:
        return r"\cr" + return_marker.group(1)
    event_wait = re.fullmatch(r"wait_event\((.*)\)", statement)
    if event_wait:
        args = [part.strip() for part in event_wait.group(1).split(",")]
        if (not args or any(not re.fullmatch(r"[a-z_]+|idle\(\d+(?:\.\d+)?\)", arg, re.IGNORECASE) for arg in args)
                or sum(arg.lower().startswith("idle(") for arg in args) > 1):
            raise ValueError(f"{location}: invalid arguments for wait_event")
        return r"\waitevent(" + ",".join(args) + ")"
    match = re.fullmatch(r"(auto|pause|idle|move|point_at|click|click_control|reset|wait_event|checkpoint|return_checkpoint|watch_menu|click_menu|toggle_outside_click)\(([^()]*)\)", statement)
    if not match:
        raise ValueError(f"{location}: unknown command {statement!r}")
    name, args = match.groups()
    args = args.strip()
    if name in ("checkpoint", "return_checkpoint"):
        if not args.isdigit():
            raise ValueError(f"{location}: checkpoint must be a non-negative integer")
        return (r"\c" if name == "checkpoint" else r"\cr") + args
    if name == "auto":
        if not args.isdigit():
            raise ValueError(f"{location}: auto requires whole seconds")
        return r"\nw" + args
    patterns = {
        "pause": r"\d+(?:\.\d+)?",
        "idle": r"\d+(?:\.\d+)?",
        "move": r"\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?(?:\s*,\s*(?:linear|out_cubic|in_out_cubic))?\s*",
        "point_at": r"\s*lyrics_button(?:\s*,\s*(?:linear|out_cubic|in_out_cubic))?\s*",
        "click": r"\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?\s*",
        "click_control": r"\s*lyrics_button\s*,\s*[a-z_][a-z_0-9]*\s*",
        "reset": r"\s*(?:linear|out_cubic|in_out_cubic)\s*",
        "wait_event": r"\s*[a-z_]+(?:\s*,\s*[a-z_]+)*\s*",
        "watch_menu": r"\s*lyrics_menu\s*,\s*[a-z_][a-z_0-9]*\s*",
        "click_menu": r"\s*lyrics_menu\s*,\s*search_lyrics\s*,\s*[a-z_][a-z_0-9]*\s*",
        "toggle_outside_click": r"\s*(?:true|false)\s*",
    }
    if not re.fullmatch(patterns[name], args, re.IGNORECASE):
        raise ValueError(f"{location}: invalid arguments for {name}")
    names = {"pause": "wait", "idle": "idle", "move": "pos", "point_at": "pointat", "click": "click", "click_control": "clickcontrol", "reset": "res", "wait_event": "waitevent", "watch_menu": "watchmenu", "click_menu": "clickmenu", "toggle_outside_click": "toggleoutsideclick"}
    return "\\" + names[name] + "(" + args + ")"


def compile_script(source):
    """Return label -> list of (speaker, encoded dialogue) without evaluating code."""
    labels = {}
    current = None
    pending = ""
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        stripped = raw.strip()
        location = f"line {i}"
        if not stripped or stripped.startswith("#"):
            continue
        label = re.fullmatch(r"label\s+([A-Za-z_][A-Za-z_0-9]*):", stripped)
        if label and not raw[:1].isspace():
            if pending:
                raise ValueError(f"{location}: unused before command")
            name = label.group(1)
            if name in labels:
                raise ValueError(f"{location}: duplicate label {name!r}")
            current = labels[name] = []
            continue
        if current is None or not raw.startswith("    "):
            raise ValueError(f"{location}: expected an indented statement inside a label")
        marker = re.fullmatch(r"checkpoint\s+(\d+)", stripped)
        if marker:
            pending += _cue(f"checkpoint({marker.group(1)})", location)
            continue
        dialogue = re.fullmatch(r'(wizard|extend)\s+("(?:[^"\\]|\\.)*")', stripped)
        if dialogue:
            text = _quoted(dialogue.group(2), location)
            if dialogue.group(1) == "extend":
                if not current or pending:
                    raise ValueError(f"{location}: extend requires a previous line")
                current[-1] = (current[-1][0], current[-1][1] + text)
            else:
                current.append(("Setup Wizard", pending + text))
                pending = ""
            continue
        if stripped.startswith("before "):
            pending += _cue(stripped[7:].strip(), location)
            continue
        if stripped.startswith("$ "):
            if not current:
                raise ValueError(f"{location}: command requires a dialogue line")
            current[-1] = (current[-1][0], current[-1][1] + _cue(stripped[2:].strip(), location))
            continue
        direct_jump = re.fullmatch(r"jump\s+([A-Za-z_][A-Za-z_0-9]*)", stripped)
        if direct_jump:
            if not current:
                raise ValueError(f"{location}: jump requires a dialogue line")
            current[-1] = (current[-1][0], current[-1][1] + rf"\jump({direct_jump.group(1)})")
            continue
        condition = re.fullmatch(r'if\s+event\s*==\s*("(?:[^"\\]|\\.)*"):', stripped)
        if condition:
            if not current or i >= len(lines):
                raise ValueError(f"{location}: if requires a dialogue line and jump")
            event = _quoted(condition.group(1), location)
            jump = re.fullmatch(r'\s{8,}jump\s+([A-Za-z_][A-Za-z_0-9]*)\s*', lines[i])
            spoken = re.fullmatch(r'\s{8,}wizard\s+("(?:[^"\\]|\\.)*")\s*', lines[i])
            if not jump and not spoken:
                raise ValueError(f"{location}: expected an indented jump or wizard line")
            i += 1
            if jump:
                cue = rf"\if({event},{jump.group(1)})"
            else:
                text = _quoted(spoken.group(1), f"line {i}")
                encoded = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
                cue = rf"\iftext({event},{encoded})"
            current[-1] = (current[-1][0], current[-1][1] + cue)
            continue
        if stripped == "menu:":
            if not current:
                raise ValueError(f"{location}: menu requires a dialogue line")
            found = False
            while i < len(lines):
                option = re.fullmatch(r'\s{8}("(?:[^"\\]|\\.)*"):\s*', lines[i])
                if not option:
                    break
                if i + 1 >= len(lines):
                    raise ValueError(f"line {i + 1}: missing menu jump")
                jump = re.fullmatch(r'\s{12,}jump\s+([A-Za-z_][A-Za-z_0-9]*)\s*', lines[i + 1])
                if not jump:
                    raise ValueError(f"line {i + 2}: expected a menu jump")
                text = _quoted(option.group(1), f"line {i + 1}")
                if any(char in text for char in ",()"):
                    raise ValueError(f"line {i + 1}: menu text cannot contain commas or parentheses")
                current[-1] = (current[-1][0], current[-1][1] + rf"\button({text},{jump.group(1)})")
                found = True
                i += 2
            if not found:
                raise ValueError(f"{location}: menu has no options")
            continue
        raise ValueError(f"{location}: unrecognized statement {stripped!r}")
    if pending:
        raise ValueError("end of file: unused before command")
    return labels


def load_script(path=SCRIPT_PATH):
    return compile_script(path.read_text(encoding="utf-8"))
