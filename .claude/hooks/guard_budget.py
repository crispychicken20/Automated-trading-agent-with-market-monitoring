"""Claude Code PreToolUse hook: the agent may never raise risk.max_total_budget_usd.

The ceiling is whatever config.yaml says right now. Edits that lower the budget are allowed;
edits that raise it, remove it, or make it unreadable are blocked (exit code 2). The hook
also blocks shell commands that could rewrite YAML config or these guard files, and running
the bot with a different --config file, since those would get around the check.

This only constrains the agent. You can still change config.yaml yourself in an editor.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KEY = "max_total_budget_usd"
PROJECT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT / "config.yaml"
PROTECTED = (".claude/", "guard_budget")
WRITE_TOKENS = (
    ">", "tee", "sed", "awk", "perl", "python", "ruby", "node", "yq", "mv ", "cp ", "rm ",
    "dd ", "ln ", "install ", "truncate", "chmod", "git checkout", "git restore", "git apply",
    "git reset", "git stash", "patch",
)


def block(reason: str) -> None:
    print(f"Blocked by budget guard: {reason}", file=sys.stderr)
    sys.exit(2)


def budget_in(text: str) -> float | None:
    """Return the risk.max_total_budget_usd in a YAML text, or None if it has none."""
    try:
        import yaml

        data = yaml.safe_load(text)
        risk = data.get("risk") if isinstance(data, dict) else None
        value = risk.get(KEY) if isinstance(risk, dict) else None
        if value is None and KEY not in text:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        return float(value)
    except ImportError:
        # Without PyYAML: every mention must be a plain `max_total_budget_usd: <number>` line.
        values = re.findall(rf"^[ \t]*{KEY}[ \t]*:[ \t]*([0-9]+(?:\.[0-9]+)?)[ \t]*(?:#.*)?$",
                            text, re.M)
        if text.count(KEY) != len(values) or len(values) > 1:
            raise ValueError
        return float(values[0]) if values else None


def ceiling() -> float:
    try:
        value = budget_in(CONFIG.read_text())
    except (OSError, ValueError, AttributeError):
        value = None
    if value is None:
        block("config.yaml has no readable risk.max_total_budget_usd; ask the user to fix it")
    return value


def check_yaml(path: Path, new_text: str) -> None:
    is_config = path.resolve() == CONFIG.resolve()
    try:
        new = budget_in(new_text)
    except Exception:
        block(f"{path.name}: {KEY} must stay a plain number in the risk section")
    if new is None:
        if is_config:
            block(f"removing risk.{KEY} from config.yaml is not allowed")
        return
    limit = ceiling()
    if new > limit:
        block(f"raising risk.{KEY} from {limit:g} to {new:g} is not allowed; only the user "
              "may raise the total budget, by editing config.yaml themselves")


def apply_edits(path: Path, edits: list[dict]) -> str:
    text = path.read_text() if path.exists() else ""
    for e in edits:
        old, new = e.get("old_string", ""), e.get("new_string", "")
        text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def main() -> None:
    event = json.load(sys.stdin)
    tool, args = event.get("tool_name", ""), event.get("tool_input", {}) or {}

    if tool == "Bash":
        cmd = args.get("command", "")
        writes = any(t in cmd for t in WRITE_TOKENS)
        if KEY in cmd:
            block(f"shell commands may not touch {KEY}; edit config.yaml with the Edit tool")
        if writes and (re.search(r"\.ya?ml\b", cmd) or any(p in cmd for p in PROTECTED)):
            block("shell commands may not write YAML config or the budget guard; "
                  "use the Edit tool so the budget can be checked")
        if re.search(r"--config\b", cmd):
            block("the bot must run with the project's config.yaml (no --config)")
        return

    raw = args.get("file_path") or args.get("notebook_path") or ""
    if not raw:
        return
    path = Path(raw) if Path(raw).is_absolute() else PROJECT / raw
    if (PROJECT / ".claude").resolve() in path.resolve().parents:
        block("the budget guard and Claude settings may only be changed by the user")
    if path.suffix not in (".yaml", ".yml"):
        return
    if tool == "Write":
        check_yaml(path, args.get("content", ""))
    elif tool == "Edit":
        check_yaml(path, apply_edits(path, [args]))
    elif tool == "MultiEdit":
        check_yaml(path, apply_edits(path, args.get("edits", [])))


if __name__ == "__main__":
    main()
