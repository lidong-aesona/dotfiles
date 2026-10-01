#!/usr/bin/env python3
"""Exercise the dispatching-tickets skill's routing and prompt scripts, and its Home Manager wiring."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "home/.agents/skills/dispatching-tickets"
ROUTE = SKILL / "scripts/route.py"
PROMPT = SKILL / "scripts/prompt.py"
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


class Estate(unittest.TestCase):
    """A scratch estate built from the shipped template, a fake HOME holding the routed skills,
    and a fake herdr whose agent list each test controls."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="dispatching-tickets-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.estate = self.root / "estate"
        (self.estate / "blocks").mkdir(parents=True)
        (self.estate / "tickets/42").mkdir(parents=True)
        shutil.copy(SKILL / "templates/estate.toml", self.estate / "estate.toml")
        for block in ("estate.md", "repo-boundary.md", "closing-mandate.md", "review.md"):
            (self.estate / "blocks" / block).write_text(f"{block} body\n")
        (self.estate / "tickets/42/head.md").write_text("build head\n")
        (self.estate / "tickets/42/review-head.md").write_text("review head\n")
        self.quota(anthropic=0.80, openai=0.40, xai=0.10)

        home = self.root / "home"
        for skill in ("implement", "implement-spec", "codebase-design", "research",
                      "diagnosing-bugs", "prototype", "wizard", "code-review"):
            (home / ".agents/skills" / skill).mkdir(parents=True)
            (home / ".agents/skills" / skill / "SKILL.md").write_text("skill\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        herdr = bin_dir / "herdr"
        herdr.write_text(textwrap.dedent("""\
            #!/bin/sh
            [ -n "$FAKE_HERDR_FAIL" ] && { echo "herdr unreachable" >&2; exit 1; }
            cat "$FAKE_HERDR_AGENTS"
            """))
        herdr.chmod(0o755)
        self.agents_file = self.root / "agents.json"
        self.live()
        self.env = dict(os.environ, HOME=str(home), PATH=f"{bin_dir}:{os.environ['PATH']}",
                        FAKE_HERDR_AGENTS=str(self.agents_file))

    def quota(self, read_at=NOW, **used):
        self.estate.joinpath("quota.toml").write_text("".join(
            f'[{p}]\nperiod_start = "2026-09-28T00:00Z"\nperiod_days = 7\nused = {u}\nread_at = "{read_at}"\n'
            for p, u in used.items()))

    def live(self, *names):
        self.agents_file.write_text(json.dumps({"result": {"agents": [{"name": n} for n in names]}}))

    def exhaust(self, profile):
        toml = self.estate / "estate.toml"
        text = toml.read_text()
        start = text.index(f"[agents.profiles.{profile}]")
        end = text.index('exhausted_until = ""', start)
        toml.write_text(text[:end] + 'exhausted_until = "2026-10-03T00:00"' + text[end + 20:])

    def run_script(self, script, *args, **env):
        return subprocess.run(["python3", str(script), str(self.estate), *args],
                              capture_output=True, text=True, env={**self.env, **env})

    def route(self, *args, **env):
        r = self.run_script(ROUTE, *args, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def refused(self, script, *args, **env):
        r = self.run_script(script, *args, **env)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        return r.stderr


class RouteTests(Estate):
    def test_every_template_type_routes(self):
        for kind, expected in {"design": "claude-opus-high", "spec-build": "claude-opus",
                               "security": "claude-fable", "research": "grok",
                               "implementation": "grok", "debugging": "grok",
                               "prototype": "grok", "mechanical": "gpt-luna",
                               "human-steps": "grok"}.items():
            with self.subTest(kind=kind):
                self.assertEqual(self.route(kind)["profile"], expected)

    def test_args_carry_the_type_skill(self):
        chosen = self.route("implementation")
        self.assertEqual(chosen["args"][-2], "--append-system-prompt")
        self.assertTrue(chosen["args"][-1].endswith(".agents/skills/implement/SKILL.md"))

    def test_review_never_runs_on_the_author_provider(self):
        for author in ("anthropic", "openai", "xai"):
            with self.subTest(author=author):
                chosen = self.route("review", "--author", author)
                self.assertNotEqual(chosen["provider"], author)

    def test_review_types_demand_an_author(self):
        self.assertIn("pass --author", self.refused(ROUTE, "review"))
        self.assertIn("for review types", self.refused(ROUTE, "implementation", "--author", "xai"))

    def test_exhausted_profiles_are_skipped(self):
        self.exhaust("grok")
        self.assertEqual(self.route("implementation")["profile"], "gpt-sol")
        self.assertIn("no candidate profile is left", self.refused(ROUTE, "research"))
        self.assertIn("marked exhausted", self.refused(PROMPT, "42", "--profile", "grok"))

    def test_stale_readings_count_as_on_pace(self):
        self.quota(read_at="2026-01-01T00:00Z", anthropic=0.0, openai=0.99, xai=0.99)
        self.assertEqual(self.route("debugging")["profile"], "gpt-astra")

    def test_live_agents_spread_one_tick(self):
        self.live(*[f"ticket-{n}g" for n in range(1, 6)], "ticket-6rg", "ticket-7o", "not-a-ticket")
        chosen = self.route("implementation")
        self.assertEqual(chosen["profile"], "grok")
        self.assertIn("6 live agents", chosen["candidates"]["grok"]["why"])
        self.live(*[f"ticket-{n}g" for n in range(1, 8)])
        self.assertEqual(self.route("implementation")["profile"], "gpt-sol")

    def test_unreadable_agent_list_refuses(self):
        self.assertIn("cannot read the agent list",
                      self.refused(ROUTE, "implementation", FAKE_HERDR_FAIL="1"))
        self.assertEqual(self.route("research", FAKE_HERDR_FAIL="1")["profile"], "grok")

    def test_estate_without_routing_uses_active(self):
        toml = self.estate / "estate.toml"
        toml.write_text(toml.read_text().split("\n[routing]\n")[0] + "\n")
        self.assertEqual(self.route("anything")["profile"], "claude-opus")


class PromptTests(Estate):
    def test_build_prompt_uses_head_and_build_blocks(self):
        out = Path(self.run_script(PROMPT, "42", "--profile", "gpt-sol").stdout.strip())
        text = out.read_text()
        self.assertTrue(text.startswith("build head"))
        self.assertIn("closing-mandate.md body", text)
        self.assertNotIn("review.md body", text)

    def test_review_prompt_never_carries_the_closing_mandate(self):
        out = Path(self.run_script(PROMPT, "42", "--review", "--profile", "gpt-astra").stdout.strip())
        self.assertEqual(out.name, "review.prompt.md")
        text = out.read_text()
        self.assertTrue(text.startswith("review head"))
        self.assertIn("review.md body", text)
        self.assertNotIn("closing-mandate.md body", text)

    def test_review_and_preface_do_not_combine(self):
        self.assertIn("do not combine", self.refused(PROMPT, "42", "--review", "--preface", "x.md"))


class WiringTests(unittest.TestCase):
    def test_home_manager_links_every_tracked_skill(self):
        nix = (ROOT / "home.nix").read_text()
        for skill in (ROOT / "home/.agents/skills").iterdir():
            with self.subTest(skill=skill.name):
                self.assertIn(f'".agents/skills/{skill.name}" = linked "home/.agents/skills/{skill.name}";', nix)

    def test_sync_skips_tracked_skills(self):
        script = (ROOT / "sync-skills.sh").read_text()
        self.assertRegex(script, re.escape('for skill in "$DIR"/home/.agents/skills/*/; do'))
        self.assertIn('--exclude "/$(basename "$skill")"', script)


if __name__ == "__main__":
    unittest.main()
