#!/usr/bin/env bash
# Exercise the shipped hook and Pi's real compactor with a local provider.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [ -n "${PI_PACKAGE_DIR:-}" ]; then
  PKG=$PI_PACKAGE_DIR
elif command -v brew >/dev/null 2>&1 && PREFIX=$(brew --prefix pi-coding-agent 2>/dev/null); then
  PKG="$PREFIX/libexec/lib/node_modules/@earendil-works/pi-coding-agent"
else
  PKG="$(npm root -g)/@earendil-works/pi-coding-agent"
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/home/.pi/agent/extensions" "$TMP/home/.claude" "$TMP/node_modules/@earendil-works"
cp "$ROOT/home/.pi/agent/extensions/compaction.js" "$TMP/home/.pi/agent/extensions/"
cp "$ROOT/home/.claude/CLAUDE.md" "$TMP/home/.claude/"
ln -s "$PKG" "$TMP/node_modules/@earendil-works/pi-coding-agent"
ln -s "$PKG/node_modules/@earendil-works/pi-ai" "$TMP/node_modules/@earendil-works/pi-ai"
printf '%s\n' '{"type":"module"}' > "$TMP/package.json"
# Test the same symlink layout used by Home Manager.
ln -s "$TMP/home/.pi/agent/extensions" "$TMP/extensions"
cd "$TMP"
node --input-type=module <<'JS'
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import extension from "./extensions/compaction.js";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";

let handler;
extension({ on(name, fn) { assert.equal(name, "session_before_compact"); handler = fn; } });
const policy = readFileSync("home/.claude/CLAUDE.md", "utf8")
  .split("## Compact instructions\n")[1].split("\n## Session notes")[0].trim();
const requests = [];
const model = { id: "local", provider: "local", api: "openai-completions", baseUrl: "http://unused", maxTokens: 1024, reasoning: false };
const provider = {
  streamSimple(requestModel, context, options) {
    requests.push({ requestModel, context, options });
    assert.ok(context.systemPrompt.endsWith(`${policy}\n\nPreserve the deployment ID.`));
    assert.ok(!context.systemPrompt.includes("## Session notes"));
    assert.equal(requestModel.baseUrl, "http://override");
    assert.equal(options.apiKey, "test-key");
    assert.equal(options.headers["x-test"], "yes");
    assert.equal(options.env.TEST, "yes");
    assert.equal(options.cacheRetention, "none");
    const stream = createAssistantMessageEventStream();
    const message = {
      role: "assistant", content: [{ type: "text", text: "CHECKPOINT" }],
      api: model.api, provider: model.provider, model: model.id, timestamp: Date.now(),
      stopReason: "stop",
      usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    queueMicrotask(() => { stream.push({ type: "done", reason: message.stopReason, message }); stream.end(message); });
    return stream;
  },
};
const ctx = {
  model, thinkingLevel: "off",
  modelRegistry: {
    async getApiKeyAndHeaders() { return { ok: true, apiKey: "test-key", headers: { "x-test": "yes" }, baseUrl: "http://override", env: { TEST: "yes" } }; },
    getProvider() { return provider; },
  },
};
const user = (content) => ({ role: "user", content, timestamp: 1 });
const preparation = {
  firstKeptEntryId: "kept", tokensBefore: 1000,
  messagesToSummarize: [user("ACTIVE_GOAL")], turnPrefixMessages: [], isSplitTurn: false,
  previousSummary: "PREVIOUS_CHECKPOINT",
  fileOps: { read: new Set(["read.ts"]), written: new Set(["written.ts"]), edited: new Set(["edited.ts"]) },
  settings: { enabled: true, reserveTokens: 1024, keepRecentTokens: 100 },
};
for (const reason of ["manual", "threshold", "overflow"]) {
  for (const split of [false, true]) {
    requests.length = 0;
    const signal = new AbortController().signal;
    const result = await handler({ reason, signal, customInstructions: "Preserve the deployment ID.", preparation: {
      ...preparation, isSplitTurn: split, turnPrefixMessages: split ? [user("TURN_PREFIX")] : [],
    } }, ctx);
    assert.equal(requests.length, split ? 2 : 1);
    assert.ok(requests.every((r) => r.options.signal === signal));
    assert.ok(JSON.stringify(requests[0].context).includes("PREVIOUS_CHECKPOINT"));
    if (split) assert.ok(JSON.stringify(requests[1].context).includes("TURN_PREFIX"));
    assert.equal(result.compaction.firstKeptEntryId, "kept");
    assert.equal(result.compaction.tokensBefore, 1000);
    assert.equal(result.compaction.usage.totalTokens, split ? 4 : 2);
    assert.deepEqual(result.compaction.details, { readFiles: ["read.ts"], modifiedFiles: ["edited.ts", "written.ts"] });
    assert.ok(result.compaction.summary.includes("<modified-files>"));
  }
}
// Missing manual instructions still applies the entire policy.
requests.length = 0;
provider.streamSimple = (requestModel, context, options) => {
  assert.ok(context.systemPrompt.endsWith(policy));
  const stream = createAssistantMessageEventStream();
  const message = { role: "assistant", content: [], stopReason: "error", errorMessage: "local failure" };
  queueMicrotask(() => { stream.push({ type: "error", reason: "error", error: message }); stream.end(message); });
  return stream;
};
await assert.rejects(handler({ preparation, signal: new AbortController().signal }, ctx), /local failure/);
console.log("PASS: manual, threshold, overflow and split-turn compaction preserve policy, focus, auth, file tracking, usage and abort signal; errors propagate");
JS
