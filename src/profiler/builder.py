"""Turn notes into a profile using the Claude Code CLI.

The CLI runs on the plan Claude Code is logged into, so no separately billed
API key is used. If ``ANTHROPIC_API_KEY`` is set, Claude Code would bill that
key instead, so the builder refuses to run.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from .prompts import SYSTEM_PROMPT, build_prompt
from .schema import Profile, ProfileError

DEFAULT_TIMEOUT = 180  # seconds


class BuilderError(RuntimeError):
    """Raised when the profile could not be built (CLI missing, failed or bad output)."""


def _extract_json(text: str) -> Any:
    """Parse the model's reply, tolerating code fences or text around the object."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise BuilderError("the AI reply did not contain a JSON object")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise BuilderError(f"the AI reply was not valid JSON: {exc}") from None


def run_claude(prompt: str, executable: str = "claude", model: str | None = None,
               timeout: int = DEFAULT_TIMEOUT) -> str:
    """Send ``prompt`` to ``claude -p`` (no tools) and return the reply text."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise BuilderError(
            "ANTHROPIC_API_KEY is set, so Claude Code would bill the API instead of your plan. "
            "Unset it to build profiles.")
    path = shutil.which(executable)
    if path is None:
        raise BuilderError(f"Claude Code CLI ({executable!r}) not found. Install it and log in first.")
    cmd = [path, "-p", "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--system-prompt", SYSTEM_PROMPT]
    if model:
        cmd += ["--model", model]
    # Run in an empty folder so the CLI doesn't pick up this project's files or CLAUDE.md.
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", timeout=timeout, cwd=workdir)
        except subprocess.TimeoutExpired:
            raise BuilderError(f"Claude Code did not answer within {timeout} seconds") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:500]
        raise BuilderError(f"Claude Code failed (exit {proc.returncode}): {detail}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise BuilderError("Claude Code returned output that wasn't JSON") from None
    if envelope.get("is_error"):
        raise BuilderError(f"Claude Code reported an error: {envelope.get('result', '')[:500]}")
    return str(envelope.get("result", ""))


def build_profile(name: str, notes: str, session_notes: list[str], **cli_options: Any) -> Profile:
    """Ask Claude for a profile and validate it. ``cli_options`` go to :func:`run_claude`."""
    if not notes.strip() and not session_notes:
        raise BuilderError(f"there are no notes on {name} yet")
    reply = run_claude(build_prompt(name, notes, session_notes), **cli_options)
    data = _extract_json(reply)
    if isinstance(data, dict):
        data["name"] = name  # the stored name wins over whatever the model wrote
        data["last_updated"] = dt.date.today().isoformat()
    try:
        return Profile.from_dict(data)
    except ProfileError as exc:
        raise BuilderError(f"the AI's profile was invalid: {exc}") from None
