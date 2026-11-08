"""Compile the supported Ren'Py-inspired onboarding script to dialogue cues.

This is a deliberately small, data-only language, not a Python or Ren'Py runtime.
"""

import json
import base64
import re
from pathlib import Path

from core.onboarding_state import encode_payload, parse_condition, state_command


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
    sound = re.fullmatch(r"sound\((.*)\)", statement)
    play_sound = re.fullmatch(r"play\s+sound\s+(.+)", statement)
    if sound or play_sound:
        name = _quoted((sound or play_sound).group(1).strip(), location)
        if not re.fullmatch(r"[a-z_0-9-]+(?:\.(?:ogg|mp3|wav))?", name, re.IGNORECASE):
            raise ValueError(f"{location}: sound must name a bundled OGG, MP3, or WAV file")
        return r"\sound(" + encode_payload(name) + ")"
    state = state_command(statement, location)
    if state is not None:
        return r"\state(" + encode_payload(state) + ")"
    fixed = {
        "next": r"\n", "end": r"\e", "continue_setup": r"\c",
        "hide_cursor": r"\curhide", "toggle_outside_click": r"\toggleoutsideclick",
        "reset": r"\res", "return": r"\return", "stop_sound": r"\soundstop", "stop sound": r"\soundstop",
    }
    if statement in fixed:
        return fixed[statement]
    call = re.fullmatch(r"call\s+([A-Za-z_][A-Za-z_0-9]*)", statement)
    if call:
        return rf"\call({call.group(1)})"
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


class _ScriptParser:
    """Parse indentation explicitly so an unselected block never leaks statements."""

    def __init__(self, source):
        # Editors may retain a UTF-8 signature when source is decoded as utf-8.
        # Remove only the file prefix, preserving dialogue and line numbers.
        source = source.removeprefix("\ufeff")
        self.rows = []
        self.index = 0
        for number, raw in enumerate(source.splitlines(), 1):
            text = raw.strip()
            if not text or text.startswith("#"):
                continue
            prefix = raw[:len(raw) - len(raw.lstrip())]
            if any(char != " " for char in prefix) or len(prefix) % 4:
                raise ValueError(f"line {number}: indentation must use groups of four spaces")
            self.rows.append((number, len(prefix), text))

    def block(self, indent):
        nodes = []
        while self.index < len(self.rows):
            number, depth, text = self.rows[self.index]
            if depth < indent:
                break
            if depth != indent:
                raise ValueError(f"line {number}: unexpected indentation")
            self.index += 1
            condition = re.fullmatch(r"if\s+(.+):", text)
            if condition:
                arms = []
                expression = parse_condition(condition.group(1), f"line {number}")
                body = self.block(indent + 4)
                if not body:
                    raise ValueError(f"line {number}: if block is empty")
                arms.append((expression, body))
                has_else = False
                while self.index < len(self.rows):
                    arm_line, arm_depth, arm_text = self.rows[self.index]
                    if arm_depth != indent:
                        break
                    elif_match = re.fullmatch(r"elif\s+(.+):", arm_text)
                    if not elif_match and arm_text != "else:":
                        break
                    if has_else:
                        raise ValueError(f"line {arm_line}: branch cannot follow else")
                    self.index += 1
                    expression = (parse_condition(elif_match.group(1), f"line {arm_line}")
                                  if elif_match else None)
                    has_else = expression is None
                    body = self.block(indent + 4)
                    if not body:
                        raise ValueError(f"line {arm_line}: conditional block is empty")
                    arms.append((expression, body))
                nodes.append(("if", number, arms))
            elif text == "random:":
                variants = []
                while self.index < len(self.rows):
                    variant_line, variant_depth, variant_text = self.rows[self.index]
                    if variant_depth < indent + 4:
                        break
                    if variant_depth != indent + 4:
                        raise ValueError(f"line {variant_line}: unexpected random variant indentation")
                    self.index += 1
                    if variant_text == "choice:":
                        body = self.block(indent + 8)
                        if not body:
                            raise ValueError(f"line {variant_line}: random choice is empty")
                    else:
                        spoken = re.fullmatch(r'(?:wizard\s+)?("(?:[^"\\]|\\.)*")', variant_text)
                        if not spoken:
                            raise ValueError(f"line {variant_line}: expected a dialogue string or choice block in random")
                        body = [("statement", variant_line, "wizard " + spoken.group(1))]
                    variants.append(body)
                if not variants:
                    raise ValueError(f"line {number}: random block is empty")
                nodes.append(("random", number, variants))
            elif text == "menu:":
                options = []
                while self.index < len(self.rows):
                    option_line, option_depth, option_text = self.rows[self.index]
                    if option_depth < indent + 4:
                        break
                    option = re.fullmatch(r'("(?:[^"\\]|\\.)*"):', option_text)
                    if option_depth != indent + 4 or not option:
                        raise ValueError(f"line {option_line}: expected a menu option")
                    self.index += 1
                    body = self.block(indent + 8)
                    if not body:
                        raise ValueError(f"line {option_line}: menu option is empty")
                    options.append((_quoted(option.group(1), f"line {option_line}"), body))
                if not options:
                    raise ValueError(f"line {number}: menu has no options")
                nodes.append(("menu", number, options))
            else:
                nodes.append(("statement", number, text))
        return nodes


def _single_branch(expression, body):
    """Keep existing encodings for the original one-statement condition syntax."""
    if len(body) != 1 or body[0][0] != "statement":
        return None
    _, number, statement = body[0]
    location = f"line {number}"
    jump = re.fullmatch(r"jump\s+([A-Za-z_][A-Za-z_0-9]*)", statement)
    spoken = re.fullmatch(r'wizard\s+("(?:[^"\\]|\\.)*")', statement)
    action = statement[2:].strip() if statement.startswith("$ ") else None
    if not jump and not spoken and action is None:
        return None
    legacy_event = (expression["name"] == "event" and expression["op"] == "=="
                    and isinstance(expression.get("value"), str)
                    and re.fullmatch(r"[a-z_]+", expression["value"], re.IGNORECASE))
    if legacy_event and jump:
        return rf"\if({expression['value']},{jump.group(1)})"
    if legacy_event and spoken:
        encoded = base64.urlsafe_b64encode(_quoted(spoken.group(1), location).encode("utf-8")).decode("ascii")
        return rf"\iftext({expression['value']},{encoded})"
    branch = {"condition": expression}
    if jump:
        branch["jump"] = jump.group(1)
    elif spoken:
        branch["text"] = _quoted(spoken.group(1), location)
    else:
        branch["command"] = _cue(action, location)
    return r"\branch(" + encode_payload(branch) + ")"


def _compile_nodes(nodes, defaults=""):
    lines = []
    pending = defaults
    before_pending = False
    control_lines = set()
    random_lines = {}

    def append_random_tail(index, value):
        variants = random_lines[index]
        for variant in variants:
            speaker, text = variant[-1]
            if text.endswith(r"\flow"):
                text = text[:-5] + value + r"\flow"
            else:
                text += value
            variant[-1] = (speaker, text)
        lines[index] = ("Setup Wizard", r"\random(" + encode_payload(variants) + ")")

    def append_cue(cue):
        nonlocal pending
        if lines:
            if len(lines) - 1 in random_lines:
                append_random_tail(len(lines) - 1, cue)
            else:
                lines[-1] = (lines[-1][0], lines[-1][1] + cue)
        else:
            pending += cue

    for kind, number, value in nodes:
        location = f"line {number}"
        if kind == "random":
            variants = [_compile_nodes(body) for body in value]
            for variant in variants:
                speaker, text = variant[0]
                variant[0] = (speaker, pending + text)
            pending = ""
            before_pending = False
            index = len(lines)
            random_lines[index] = variants
            control_lines.add(index)
            lines.append(("Setup Wizard", r"\random(" + encode_payload(variants) + ")"))
            continue
        if kind == "if":
            cue = _single_branch(*value[0]) if len(value) == 1 else None
            if cue is None:
                arms = [{"condition": condition, "lines": _compile_nodes(body)}
                        for condition, body in value]
                cue = r"\choose(" + encode_payload(arms) + ")"
            append_cue(cue)
            continue
        if kind == "menu":
            jumps = [re.fullmatch(r"jump\s+([A-Za-z_][A-Za-z_0-9]*)", body[0][2])
                     if len(body) == 1 and body[0][0] == "statement" else None
                     for _, body in value]
            if all(jumps) and all(text and not any(char in text for char in ",()") for text, _ in value):
                for (text, _), jump in zip(value, jumps):
                    append_cue(rf"\button({text},{jump.group(1)})")
            else:
                options = [{"text": text, "lines": _compile_nodes(body)} for text, body in value]
                append_cue(r"\choices(" + encode_payload(options) + ")")
            continue

        statement = value
        marker = re.fullmatch(r"checkpoint\s+(\d+)", statement)
        if marker:
            pending += _cue(f"checkpoint({marker.group(1)})", location)
            before_pending = True
            continue
        dialogue = re.fullmatch(r'(wizard|extend)\s+("(?:[^"\\]|\\.)*")', statement)
        if dialogue:
            text = _quoted(dialogue.group(2), location)
            if dialogue.group(1) == "extend":
                if not lines:
                    raise ValueError(f"{location}: extend requires a previous line")
                if len(lines) - 1 in random_lines:
                    append_random_tail(len(lines) - 1, pending + text)
                else:
                    lines[-1] = (lines[-1][0], lines[-1][1] + pending + text)
                pending = ""
                before_pending = False
            else:
                lines.append(("Setup Wizard", pending + text))
                pending = ""
                before_pending = False
            continue
        if statement.startswith("before "):
            pending += _cue(statement[7:].strip(), location)
            before_pending = True
        elif statement.startswith("$ "):
            append_cue(_cue(statement[2:].strip(), location))
        elif statement.startswith("play sound ") or statement == "stop sound":
            append_cue(_cue(statement, location))
        elif re.fullmatch(r"jump\s+[A-Za-z_][A-Za-z_0-9]*", statement):
            append_cue(r"\jump(" + statement.split()[1] + ")")
        elif statement == "return" or re.fullmatch(r"call\s+[A-Za-z_][A-Za-z_0-9]*", statement):
            # Bare call/return run after the preceding dialogue is advanced.
            # Dollar-prefixed cues retain the existing inline-action behavior.
            control_lines.add(len(lines))
            lines.append(("Setup Wizard", pending + _cue(statement, location)))
            pending = ""
        else:
            raise ValueError(f"{location}: unrecognized statement {statement!r}")

    if before_pending:
        raise ValueError("end of block: unused before command")
    if pending and nodes:
        # A block containing only commands must continue without a blank click.
        lines.append(("Setup Wizard", pending + r"\flow"))
    for index in control_lines:
        lines[index] = (lines[index][0], lines[index][1] + r"\flow")
    return lines


def compile_script(source):
    """Return label -> dialogue lines; compile blocks without evaluating code."""
    parser = _ScriptParser(source)
    labels = {}
    defaults = ""
    while parser.index < len(parser.rows):
        number, depth, statement = parser.rows[parser.index]
        parser.index += 1
        location = f"line {number}"
        default = re.fullmatch(r"default\s+([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.+)", statement)
        if not depth and default and not labels:
            defaults += _cue(f"default({default.group(1)}, {default.group(2)})", location)
            continue
        label = re.fullmatch(r"label\s+([A-Za-z_][A-Za-z_0-9]*):", statement)
        if depth or not label:
            raise ValueError(f"{location}: expected a label or an initial default")
        name = label.group(1)
        if name in labels:
            raise ValueError(f"{location}: duplicate label {name!r}")
        labels[name] = _compile_nodes(parser.block(4), defaults)
    return labels


def load_script(path=SCRIPT_PATH):
    return compile_script(path.read_text(encoding="utf-8-sig"))
