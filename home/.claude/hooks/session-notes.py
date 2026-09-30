#!/usr/bin/env python3
"""Expose and restore the current Claude session's notes on macOS and Linux."""

import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

CONTEXT_LIMIT = 9000


def main():
    try:
        event = json.load(sys.stdin)
        session_id = event.get("session_id", "")
        source = event.get("source", "")
        if source not in {"startup", "resume", "compact", "clear", "fork"}:
            return
        if not isinstance(session_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", session_id
        ):
            raise ValueError("invalid session identifier")

        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        notes_dir = config_dir / "session-notes"
        notes_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        notes = notes_dir / f"{session_id}.md"
        context = (
            f"Session notes path: {notes}\n"
            "Use only this path for the current session's milestone notes. "
            "Keep notes concise and update them as described in CLAUDE.md.\n"
        )
        if source == "clear":
            context += "This session was cleared. Replace any old notes for the new task.\n"
        elif notes.is_file():
            updated = datetime.fromtimestamp(notes.stat().st_mtime, timezone.utc)
            context += (
                f"Saved checkpoint (updated {updated.isoformat(timespec='seconds')}):\n"
                "Newer user instructions and verified evidence take precedence. "
                "Recheck changing external state before acting.\n\n"
            )
            with notes.open(encoding="utf-8") as stream:
                content = stream.read(CONTEXT_LIMIT + 1)
            suffix = "\n[Notes truncated; read the session notes file for the rest.]\n"
            available = max(0, CONTEXT_LIMIT - len(context) - len(suffix))
            context += content[:available]
            if len(content) > available:
                context += suffix
        else:
            context += "No saved notes exist for this session yet.\n"

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }, ensure_ascii=False))
    except (OSError, ValueError, TypeError, AttributeError):
        print("session-notes: could not restore notes; continuing without them", file=sys.stderr)


if __name__ == "__main__":
    main()
