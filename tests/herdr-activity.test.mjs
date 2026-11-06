// Checks the file activity recorder that the herdr filetree mod reads:
// the Claude hook (home/.claude/hooks/herdr-activity.mjs) run as Claude runs it, through a link,
// and the Pi extension (home/.pi/agent/extensions/herdr-activity.js) driven by a fake Pi.
// Run with: node --test tests/herdr-activity.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TMP = mkdtempSync(join(tmpdir(), "herdr-activity-"));
process.on("exit", () => rmSync(TMP, { recursive: true, force: true }));

// A home like the one Home Manager links, with its own state folder.
const HOME = join(TMP, "home"), STATE = join(TMP, "state");
mkdirSync(join(HOME, ".claude/hooks"), { recursive: true });
symlinkSync(join(ROOT, "home/.claude/hooks/herdr-activity.mjs"), join(HOME, ".claude/hooks/herdr-activity.mjs"));
const HOOK = join(HOME, ".claude/hooks/herdr-activity.mjs");
process.env.HOME = HOME;
process.env.HERDR_FILETREE_STATE = STATE;

// A repository with two files.
const REPO = join(TMP, "repo");
mkdirSync(join(REPO, "src"), { recursive: true });
writeFileSync(join(REPO, "src/a.txt"), "alpha\n");
writeFileSync(join(REPO, "b.txt"), "beta\n");
const run = (cmd, args) => spawnSync(cmd, args, { cwd: REPO, encoding: "utf8" });
run("git", ["init", "-q"]);
run("git", ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start"]);

const events = () => {
  try { return readFileSync(join(STATE, "activity.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)); } catch { return []; }
};
const fresh = () => rmSync(STATE, { recursive: true, force: true });
const claude = (input) => {
  const r = spawnSync("node", [HOOK], { input: JSON.stringify({ cwd: REPO, session_id: "s1", tool_use_id: "u1", ...input }), encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
};

test("the Claude hook records reads, searches and writes through its link", () => {
  fresh();
  claude({ hook_event_name: "PostToolUse", tool_name: "Read", tool_input: { file_path: "src/a.txt" } });
  claude({ hook_event_name: "PostToolUse", tool_name: "Grep", tool_input: { pattern: "a" }, tool_response: { filenames: [join(REPO, "src/a.txt"), "b.txt"] } });
  claude({ hook_event_name: "PostToolUse", tool_name: "Edit", tool_input: { file_path: join(REPO, "b.txt") } });
  claude({ hook_event_name: "PostToolUseFailure", tool_name: "Edit", tool_input: { file_path: join(REPO, "b.txt") } });
  assert.deepEqual(events().map((e) => [e.kind, e.paths]), [
    ["read", [join(REPO, "src/a.txt")]],
    ["read", [join(REPO, "src/a.txt"), join(REPO, "b.txt")]],
    ["write", [join(REPO, "b.txt")]],
  ]);
  assert.deepEqual(Object.keys(events()[0]).sort(), ["at", "cwd", "id", "kind", "paths", "session"]);
});

test("the Claude hook records prompt mentions and shell commands", () => {
  fresh();
  claude({ hook_event_name: "UserPromptSubmit", prompt: "look at @src/a.txt, then go" });
  claude({ hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: { command: "cat b.txt" } });
  claude({ hook_event_name: "PostToolUse", tool_name: "Bash", tool_input: { command: "cat b.txt" }, tool_response: { stdout: "beta\n" } });
  claude({ hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: { command: "touch c.txt" } });
  claude({ hook_event_name: "PostToolUse", tool_name: "Bash", tool_input: { command: "touch c.txt" }, tool_response: { stdout: "" } });
  assert.deepEqual(events().map((e) => [e.kind, e.paths]), [
    ["mention", [join(REPO, "src/a.txt")]],
    ["read", [join(REPO, "b.txt")]],
    ["bash-start", undefined],
    ["bash-end", undefined],
  ]);
});

test("the Claude hook records a commit with its files", () => {
  fresh();
  const command = "git add src/a.txt && git commit -q -m one";
  claude({ hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: { command } });
  assert.equal(run("bash", ["-c", `${command.replace("git commit", "git -c user.name=t -c user.email=t@t commit")}`]).status, 0);
  claude({ hook_event_name: "PostToolUse", tool_name: "Bash", tool_input: { command }, tool_response: { stdout: "" } });
  const git = events().filter((e) => e.kind === "git");
  assert.deepEqual(git.map((e) => e.state), ["running", "done"]);
  assert.match(git[1].detail, /^[0-9a-f]+ one$/);
  assert.deepEqual(git[1].paths.map((p) => p.slice(-9)), ["src/a.txt"]);
  assert.ok(git[1].actions.some((a) => a.verb === "git commit"));
});

test("the Claude hook exits 0 and writes nothing for input it cannot read", () => {
  fresh();
  const r = spawnSync("node", [HOOK], { input: "not json", encoding: "utf8" });
  assert.equal(r.status, 0);
  assert.deepEqual(events(), []);
});

test("the Pi extension records the same events from Pi's tool events", async () => {
  fresh();
  const handlers = {};
  const { default: extension } = await import(pathToFileURL(join(ROOT, "home/.pi/agent/extensions/herdr-activity.js")).href);
  extension({ on: (name, fn) => { handlers[name] = fn; } });
  const ctx = { cwd: REPO, sessionManager: { getSessionId: () => "p1" } };
  const text = (t) => [{ type: "text", text: t }];
  await handlers.input({ text: "see @b.txt" }, ctx);
  await handlers.tool_result({ toolName: "read", toolCallId: "c1", input: { path: "@src/a.txt" }, content: text("alpha") }, ctx);
  await handlers.tool_result({ toolName: "grep", toolCallId: "c2", input: { pattern: "a", path: "src" }, content: text("a.txt:1: alpha") }, ctx);
  await handlers.tool_result({ toolName: "find", toolCallId: "c3", input: { pattern: "*.txt" }, content: text("No files found") }, ctx);
  await handlers.tool_result({ toolName: "edit", toolCallId: "c4", input: { path: "b.txt" }, content: text("ok") }, ctx);
  await handlers.tool_result({ toolName: "write", toolCallId: "c5", isError: true, input: { path: "b.txt" }, content: text("denied") }, ctx);
  await handlers.tool_call({ toolName: "bash", toolCallId: "c6", input: { command: "touch d.txt" } }, ctx);
  await handlers.tool_result({ toolName: "bash", toolCallId: "c6", input: { command: "touch d.txt" }, content: text("") }, ctx);
  assert.deepEqual(events().map((e) => [e.kind, e.id, e.paths]), [
    ["mention", "", [join(REPO, "b.txt")]],
    ["read", "c1", [join(REPO, "src/a.txt")]],
    ["read", "c2", [join(REPO, "src/a.txt")]],
    ["read", "c3", [REPO]],
    ["write", "c4", [join(REPO, "b.txt")]],
    ["bash-start", "c6", undefined],
    ["bash-end", "c6", undefined],
  ]);
  assert.ok(events().every((e) => e.session === "p1" && e.cwd === REPO));
});
