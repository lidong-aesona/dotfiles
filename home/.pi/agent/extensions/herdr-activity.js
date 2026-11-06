// Pi file activity for the herdr filetree mod: the same records the Claude hook writes,
// from ~/.claude/hooks/herdr-activity.mjs (a module outside this folder, which Pi loads whole).
import { statSync } from "node:fs";
import { homedir } from "node:os";
import { pathToFileURL } from "node:url";

const MODULE = `${process.env.HOME || homedir()}/.claude/hooks/herdr-activity.mjs`;

const text = (content) => (content ?? []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
const isDir = (path) => { try { return statSync(path).isDirectory(); } catch { return false; } };

export default function (pi) {
  let activity;
  const load = () => (activity ??= import(pathToFileURL(MODULE).href).catch(() => null));
  const base = (event, ctx) => ({ at: Date.now(), cwd: ctx.cwd, session: ctx.sessionManager?.getSessionId?.() ?? "", id: event.toolCallId ?? "" });
  // Pi accepts "@path" for a path argument.
  const path = (a, ctx, p) => (typeof p === "string" && p ? a.resolve(ctx.cwd, p.replace(/^@/, "")) : null);
  const record = async (make) => {
    try {
      const a = await load();
      if (a) a.emit(make(a));
    } catch {}
  };

  pi.on("input", (event, ctx) => record((a) => a.mentions(base(event, ctx), event.text ?? "")));

  pi.on("tool_call", (event, ctx) => {
    if (event.toolName !== "bash") return;
    return record((a) => a.bashStart(base(event, ctx), String(event.input?.command ?? "")));
  });

  pi.on("tool_result", (event, ctx) => record((a) => {
    const b = base(event, ctx), input = event.input ?? {}, ok = !event.isError;
    switch (event.toolName) {
      case "read":
        return [{ ...b, kind: "read", paths: [path(a, ctx, input.path)].filter(Boolean) }];
      case "write":
      case "edit":
        return ok ? [{ ...b, kind: "write", paths: [path(a, ctx, input.path)].filter(Boolean) }] : [];
      case "grep":
      case "find": {
        // Both print paths relative to the folder searched.
        const where = path(a, ctx, input.path) ?? ctx.cwd;
        const hits = ok && isDir(where) ? a.outputPaths(where, text(event.content)).filter((p) => p.startsWith(`${where}/`)).slice(0, 60) : [];
        return [{ ...b, kind: "read", paths: hits.length ? hits : [where] }];
      }
      case "ls":
        return [{ ...b, kind: "read", paths: [path(a, ctx, input.path) ?? ctx.cwd] }];
      case "bash":
        return a.bashEnd(b, String(input.command ?? ""), ok, text(event.content));
      default:
        return [];
    }
  }));
}
