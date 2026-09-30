import { readFileSync, realpathSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { compact } from "@earendil-works/pi-coding-agent";

// Resolve the out-of-store symlink before finding the authoritative Claude rules.
const rulesPath = resolve(
  dirname(realpathSync(fileURLToPath(import.meta.url))),
  "../../../.claude/CLAUDE.md",
);

export default function (pi) {
  pi.on("session_before_compact", async (event, ctx) => {
    const rules = readFileSync(rulesPath, "utf8");
    const instructions = rules.match(/\n## Compact instructions\s*\n([\s\S]*?)(?=\n## |$)/)?.[1]?.trim();
    if (!instructions) throw new Error(`Missing Compact instructions in ${rulesPath}`);
    if (!ctx.model) throw new Error("No model selected for compaction");

    const auth = await ctx.modelRegistry.getApiKeyAndHeaders(ctx.model);
    if (!auth.ok) throw new Error(auth.error);
    const model = auth.baseUrl ? { ...ctx.model, baseUrl: auth.baseUrl } : ctx.model;
    const provider = ctx.modelRegistry.getProvider(model.provider);
    if (!provider) throw new Error(`Missing compaction provider: ${model.provider}`);

    const policy = [instructions, event.customInstructions].filter(Boolean).join("\n\n");
    // Use Pi's own compactor to retain cut points, prior summaries, file tracking,
    // usage accounting and cancellation. Inject into every summary request,
    // including split-turn prefixes, which do not receive customInstructions.
    const result = await compact(
      event.preparation,
      model,
      auth.apiKey,
      auth.headers,
      undefined,
      event.signal,
      ctx.thinkingLevel,
      (requestModel, context, options) => provider.streamSimple(
        requestModel,
        { ...context, systemPrompt: `${context.systemPrompt ?? ""}\n\n${policy}` },
        options,
      ),
      auth.env,
    );
    return { compaction: result };
  });
}
