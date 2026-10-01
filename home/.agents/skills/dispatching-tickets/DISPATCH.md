# Dispatching one ticket

One ticket, one agent, one worktree per repository, one branch name, one profile. Scripts are in `~/.agents/skills/dispatching-tickets/scripts/`.

## 1. Place it

With more than one repository, read `blocks/repo-boundary.md` and decide yourself which repository each part of the ticket belongs in. **The ticket is not the authority on placement**: tickets are written in the tracker's repository and name its paths, even for work that belongs elsewhere. The agent starts in the repository holding most of the work; a ticket spanning repositories gets a worktree in each.

Every branch, in every repository, is `build/<n>-<slug>`. The number in the name is what protects a secondary worktree, which has no agent in it, from cleanup.

## 2. Route it

Classify the ticket and choose its profile per [ROUTING.md](ROUTING.md). The rest of this file uses the profile `route.py` printed: its `kind`, `suffix` and `args`.

## 3. Write the head

`<estate>/tickets/<n>/head.md` carries what only this ticket needs; whatever applies to every ticket belongs in a block. Read the ticket, the closing comments of its blockers, and the neighbours it builds on, then write the delta the body does not already say well:

- `Type: <type>` and `Profile: <profile> (<reason>)`, first, as ROUTING.md says.
- `Work ticket #<n> in <tracker>: "<title>". Read it in full: gh issue view <n> --repo <tracker>`.
- Where the agent is: repository, branch, base. For a spanning ticket, which part goes where, and one pull request per repository.
- Any ordering rule in the ticket that overrides the order of its criteria.
- The context behind the ticket: the decision or revert that created it, and what landed on neighbouring tickets, with where that evidence lives.
- What to read, in order, before writing anything.
- The traps: what is easiest to build backwards, words the domain docs rule out, questions to settle before building.
- Measured facts to build on, each marked to re-verify.
- Its prerequisites, to verify before writing. A missing one is a finding that graduates to a successor ticket, never something to manufacture.
- Acts specific to this ticket that need the user's approval first.
- Skills to call beyond the one the profile starts with, from the type's row in ROUTING.md, that this agent kind can load (see Agent kinds).

## 4. Assemble

```
python3 ~/.agents/skills/dispatching-tickets/scripts/prompt.py <estate> <n> --profile <profile>
```

Prints the path of `tickets/<n>/prompt.md`: the head, then the estate's blocks, then the profile's blocks. A refusal names the missing part; write that part.

## 5. Create the worktree

```
herdr worktree create --cwd <repo path> --branch build/<n>-<slug> --base origin/<base> \
  --label "<n> <short title>" --no-focus --trust-repository
```

It reports the workspace and pane it opened. A secondary repository's worktree is created the same way, with no agent.

**Pass the remote ref as `--base`, after a `git fetch origin`.** Verified 2026-09-24: `--base origin/<base>` gives the remote tip.

**Check the base after creating it.** On 2026-09-24, `--base main` branched from a stale local ref rather than the remote tip, in a primary worktree and in secondary worktrees of two repositories, so it is not about primary or secondary: herdr branches from the local base ref. Compare `git -C <worktree> rev-parse HEAD` with `origin/<base>`; on a mismatch, tell the agent to `git reset --hard origin/<base>` before it writes anything, or run `git -C <repo> fetch origin` and move the local base ref first.

## 6. Start the agent

From the profile `route.py` chose, with the `args` it printed, which already carry the type's skill file:

```
herdr agent start <agent_prefix><n><suffix> --kind <kind> --pane <pane> --timeout <timeout_ms> -- <args, one word each>
```

## 7. Submit the prompt

```
herdr agent prompt <name> "$(cat <estate>/tickets/<n>/prompt.md)"
```

Read the tail and confirm the agent is working on this ticket.

## Agent kinds

Behaviour under herdr as verified in September 2026. Re-verify when herdr or the agent updates, and record what changed here.

- **Every kind**: multi-line text cannot be passed as an agent argument; `--append-system-prompt "$(cat file)"` fails with `invalid_agent_argument`. Pass files by absolute path.
- **claude**: `--append-system-prompt-file <absolute path>` injects a skill file. The first start in a directory claude has not yet trusted returns `agent_not_ready` and sits on the Bypass Permissions warning; clear it with `herdr agent send-keys <name> down`, then `herdr agent send-keys <name> enter`. Later starts come up idle.
- **pi**: GPT runs as `--provider openai-codex` and Grok as `--provider xai`, both on subscription OAuth; `pi auth` shows which providers are signed in. `--append-system-prompt` takes literal text or a file path, and injects the file's contents. pi loads `~/.claude/skills` as model-invocable skills only, with no slash-command expansion, so a skill marked `disable-model-invocation: true` (such as `implement`) cannot be reached by name: deliver it through the system prompt, and leave it out of the user prompt. Skills without that flag (`grilling`, `domain-modeling`, `tdd`) are discovered and can be named. When the delivered procedure names a skill the kind cannot load (such as `/code-review`), a profile block names the substitute: re-read your own diff as a reviewer would before you land, and fix what you would flag.
- **codex**: unverified under herdr. Before its first dispatch, start one by hand with `-m <model> -c model_reasoning_effort=<effort>` and record here how it takes a skill file and what its crashed and exhausted tails look like.
