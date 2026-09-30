#!/usr/bin/env python3
"""Exercise the shipped hook and Linux settings merge with disposable fixtures."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "home/.claude/hooks/session-notes.py"
SHARED = ROOT / "home/.claude/settings.json"
MERGE = ROOT / "home/.claude/shared-settings.jq"


class SessionNotesTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="claude-context-")
        self.addCleanup(self.scratch.cleanup)
        self.config = Path(self.scratch.name) / "config with spaces"
        self.env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.config))

    def invoke(self, session="session-a", source="startup", raw=None):
        event = {
            "session_id": session,
            "source": source,
            "hook_event_name": "SessionStart",
            "cwd": "/same/shared/checkout",
        }
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(event) if raw is None else raw,
            text=True, encoding="utf-8", capture_output=True, env=self.env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout:
            return "", result
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        return output["additionalContext"], result

    def save(self, session, content):
        directory = self.config / "session-notes"
        directory.mkdir(parents=True, exist_ok=True)
        notes = directory / f"{session}.md"
        notes.write_text(content, encoding="utf-8")
        return notes

    def test_startup_exposes_current_path_without_creating_empty_notes(self):
        context, result = self.invoke()
        self.assertIn(str(self.config / "session-notes/session-a.md"), context)
        self.assertIn("No saved notes", context)
        self.assertEqual(result.stderr, "")
        self.assertFalse((self.config / "session-notes/session-a.md").exists())
        self.assertEqual(stat.S_IMODE((self.config / "session-notes").stat().st_mode), 0o700)

    def test_startup_resume_and_compact_restore_same_checkpoint_without_rewriting_it(self):
        notes = self.save("session-a", "# Goal\nFinish the UI repro\n")
        before = notes.stat().st_mtime_ns
        for source in ("startup", "resume", "compact"):
            with self.subTest(source=source):
                context, result = self.invoke(source=source)
                self.assertIn("Finish the UI repro", context)
                self.assertIn("updated ", context)
                self.assertIn("Newer user instructions", context)
                self.assertEqual(result.stderr, "")
        self.assertEqual(notes.stat().st_mtime_ns, before)

    def test_shared_checkout_and_fork_keep_session_notes_separate(self):
        self.save("session-a", "ONLY SESSION A")
        self.save("session-b", "ONLY SESSION B")
        context, _ = self.invoke("session-b", "resume")
        self.assertIn("ONLY SESSION B", context)
        self.assertNotIn("ONLY SESSION A", context)
        forked, _ = self.invoke("session-c", "fork")
        self.assertIn("session-c.md", forked)
        self.assertNotIn("ONLY SESSION A", forked)
        self.assertNotIn("ONLY SESSION B", forked)

    def test_clear_hides_old_task_notes_without_deleting_them(self):
        notes = self.save("session-a", "OLD TASK")
        context, _ = self.invoke(source="clear")
        self.assertNotIn("OLD TASK", context)
        self.assertIn("Replace any old notes", context)
        self.assertEqual(notes.read_text(), "OLD TASK")

    def test_oversized_unicode_notes_bound_context_without_splitting_characters(self):
        self.save("session-a", "调查 🔬 café\n" * 4000)
        context, result = self.invoke(source="compact")
        self.assertLessEqual(len(context), 9000)
        self.assertIn("调查 🔬 café", context)
        self.assertIn("Notes truncated", context)
        self.assertNotIn("\ufffd", context)
        self.assertEqual(result.stderr, "")

    def test_invalid_payloads_and_path_traversal_skip_without_creating_notes(self):
        payloads = [
            "{broken", "[]", "null",
            '{"source":"startup"}',
            '{"source":"startup","session_id":"../../outside"}',
            '{"source":"startup","session_id":42}',
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                context, result = self.invoke(raw=payload)
                self.assertEqual(context, "")
                self.assertIn("continuing without them", result.stderr)
        self.assertFalse(self.config.exists())

    def test_unrelated_source_is_ignored(self):
        context, result = self.invoke(source="unrelated")
        self.assertEqual(context, "")
        self.assertEqual(result.stderr, "")
        self.assertFalse(self.config.exists())

    def test_unreadable_notes_fail_without_blocking_session(self):
        notes = self.save("session-a", "")
        notes.write_bytes(b"\xff\xfe")
        context, result = self.invoke(source="compact")
        self.assertEqual(context, "")
        self.assertIn("continuing without them", result.stderr)


class SharedSettingsTests(unittest.TestCase):
    def merge(self, local, shared=SHARED):
        result = subprocess.run(
            ["jq", "--slurpfile", "shared", str(shared), "-f", str(MERGE)],
            input=json.dumps(local), text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_local_policy_and_other_hooks_survive_repeated_merges(self):
        local_start = {"matcher": "resume", "hooks": [{"type": "command", "command": "local-start"}]}
        local_stop = [{"hooks": [{"type": "command", "command": "local-stop"}]}]
        local = {
            "permissions": {"allow": ["Read(/home/ec2-user/project/**)"]},
            "autoMode": {"allow": ["local-policy"]},
            "apiKeyHelper": "local-credential-helper",
            "hooks": {"SessionStart": [local_start], "Stop": local_stop},
            "theme": "old-theme",
        }
        merged = self.merge(local)
        for key in ("permissions", "autoMode", "apiKeyHelper"):
            self.assertEqual(merged[key], local[key])
        self.assertEqual(merged["hooks"]["Stop"], local_stop)
        self.assertEqual(merged["hooks"]["SessionStart"][0], local_start)
        self.assertEqual(len(merged["hooks"]["SessionStart"]), 2)
        self.assertEqual(merged["autoCompactWindow"], 300000)
        self.assertEqual(self.merge(merged), merged)

    def test_new_machine_gets_shared_settings_and_hook(self):
        merged = self.merge({})
        shared = json.loads(SHARED.read_text())
        self.assertEqual(merged["hooks"]["SessionStart"], shared["hooks"]["SessionStart"])
        self.assertEqual(merged["autoCompactWindow"], 300000)
        self.assertNotIn("autoMode", merged)
        self.assertNotIn("permissions", merged)

    def test_shared_file_without_hooks_does_not_create_or_remove_local_hooks(self):
        with tempfile.TemporaryDirectory(prefix="claude-settings-") as scratch:
            shared = Path(scratch) / "settings.json"
            shared.write_text('{"theme": "dark"}')
            self.assertEqual(self.merge({}, shared), {"theme": "dark"})
            local = {"hooks": {"Stop": []}}
            self.assertEqual(self.merge(local, shared), {**local, "theme": "dark"})

    def test_configured_hook_runs_from_another_working_directory(self):
        shared = json.loads(SHARED.read_text())
        command = shared["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        command = command.replace("$HOME/.claude/hooks/session-notes.py", str(HOOK))
        with tempfile.TemporaryDirectory(prefix="claude-command-") as scratch:
            result = subprocess.run(
                command, shell=True, cwd=scratch,
                input='{"session_id":"command-session","source":"startup"}',
                text=True, capture_output=True,
                env=dict(os.environ, CLAUDE_CONFIG_DIR=scratch),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("command-session.md", context)


if __name__ == "__main__":
    unittest.main(verbosity=2)
