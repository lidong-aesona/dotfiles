# Dispatching one ticket

One ticket, one agent, one worktree per repository, one branch name, one profile. Scripts are in `~/.agents/skills/dispatching-tickets/scripts/`.

## 1. Place it

With more than one repository, read `blocks/repo-boundary.md` and decide yourself which repository each part of the ticket belongs in. **The ticket is not the authority on placement**: tickets are written in the tracker's repository and name its paths, even for work that belongs elsewhere. The agent starts in the repository holding most of the work; a ticket spanning repositories gets a worktree in each.

Every branch, in every repository, is `build/<key>-<slug>`. The key in the name is what protects a secondary worktree, which has no agent in it, from cleanup. A main-tracker ticket's key is its number (`build/1511-x`); a ticket on a tracker listed in estate `[[trackers]]` is keyed by that tracker's prefix and number (`build/r235-x` for `aesona/research#235`). The same key names the agent (`<agent_prefix><key><suffix>`), the worktree directory (`build-<key>-<slug>`) and `tickets/<key>/`, and is what `dispatch.py` and `prompt.py` take. Never name a prefixed ticket by its bare number: the numbers collide across trackers, and cleanup would read the session as the other tracker's ticket.

## 2. Route it

Use the frontier decision: VM eligibility is `[labels].dispatch = "fleet:w1"`, with its original label precedence, plus `[labels].dispatch_requires` (currently `ready-for-agent`) for a fresh dispatch. Mac work requires `fleet:mac` plus `[machines.mac].dispatch_requires` (currently `ready-for-agent`) and no `ready-for-human`; human-labelled Mac work surfaces for hand dispatch. Both fleet labels together hold. Take the machine from `frontier.py` and `[machine_labels]` in estate.toml. `fleet:mac` alone never authorizes an automatic agent. Local work chooses its profile per [ROUTING.md](ROUTING.md); Mac work uses `[machines.mac].profile`, currently pi, openai-codex, gpt-6.1-sol, medium. Write that profile in the head. The local commands below use the profile `route.py` printed: its `kind`, `suffix` and `args`.

## 3. Write the head

`<estate>/tickets/<n>/head.md` carries what only this ticket needs; whatever applies to every ticket belongs in a block. Read the ticket, the closing comments of its blockers, and the neighbours it builds on, then write the delta the body does not already say well:

- `Type: <type>` and `Profile: <profile> (<reason>)`, first, as ROUTING.md says; `prompt.py` refuses a head without a known type.
- `Work ticket #<n> in <tracker>: "<title>". Read it in full: gh issue view <n> --repo <tracker>`, with the issue's own tracker and number (for key `r235`, `aesona/research` and `235`).
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

## Mac launch

After writing the head, for a `machine = "mac"` dispatch row run:

```
python3 ~/.agents/skills/dispatching-tickets/scripts/dispatch.py <estate> <n> <repo-name> <slug>
```

This replaces steps 4-7 for Mac work: it calls the same prompt assembler with the machine profile, prepends the Mac offline-only and shared gate-lock instructions, fetches origin, creates from the fetched `origin/main` SHA, verifies HEAD, records ownership in the head, starts `mac-<n>-codex` with the configured implement skill file through `hc run --on mac`, waits for it to come up idle, then pastes the prompt through `hc send`. Prompts never travel as SSH command text. Read its tail and confirm working. No fresh launch over an existing agent or worktree, on either machine; recover instead. A failure after create leaves an orphan for the next tick, without resetting work.

The helper launches the primary repository. Use the configured Mac repository paths, not the VM paths in shared blocks. Any additional repository worktree must carry the same ticket number and be recorded in the head; all-machine ownership and retirement guards cover those trees too. If the Mac cannot be read, follow MACHINES.md's fallback, not the local steps.

## 5. Create the worktree (local)

```
git -C <repo path> fetch origin
git -C <repo path> worktree add -b build/<key>-<slug> ~/.herdr/worktrees/<repo name>/build-<key>-<slug> origin/<base>
```

A secondary repository's worktree is created the same way, with no agent. Branching from the fetched remote ref gives the remote tip; never branch from a local base ref, which can be stale.

## 6. Start the agent (local)

From the profile `route.py` chose, with the `args` it printed, which already carry the type's skill file:

```
hc run --bg --name <agent_prefix><n><suffix> --cwd <worktree> --no-parent --outlive-parent \
  --label herdr.space=ticket-<key> -- <kind> <args, one word each>
hc wait <name> --timeout <timeout_ms / 1000>
```

`--no-parent --outlive-parent` keep the agent alive after the tick that started it ends. The `herdr.space` label shows it in the Herdr app, in one space per ticket. The wait ends when the agent first goes idle; confirm with `hc get --json <name>` that `status.state` is `idle` before submitting. Names are 1-32 of `a-z 0-9 - _`.

## 7. Submit the prompt (local)

```
hc send <name> "$(cat <estate>/tickets/<n>/prompt.md)"
```

Read the tail with `hc read <name> --lines 40` and confirm the agent is working on this ticket.

## Agent kinds

Behaviour verified under herdr in September 2026 and under hc 0.1.13 in October 2026. Re-verify when hc or the agent updates, and record what changed here.

- **Every kind**: pass skill and prompt files by absolute path, never as multi-line arguments. hc reports an agent's status only once the agent integrates with it: pi needs 1.4.0 or later (0.99 never reports, so `hc wait` times out).
- **claude**: `--append-system-prompt-file <absolute path>` injects a skill file. The first start in a directory claude has not yet trusted sits on the Bypass Permissions warning, so `hc wait` ends without an idle status; read the tail, then clear it with `hc keys <name> down enter` (the keys verified under herdr, not yet under hc). Later starts come up idle.
- **pi**: GPT runs as `--provider openai-codex` and Grok as `--provider xai`, both on subscription OAuth; `pi auth` shows which providers are signed in. `--append-system-prompt` takes literal text or a file path, and injects the file's contents. pi loads `~/.claude/skills` as model-invocable skills only, with no slash-command expansion, so a skill marked `disable-model-invocation: true` (such as `implement`) cannot be reached by name: deliver it through the system prompt, and leave it out of the user prompt. Skills without that flag (`grilling`, `domain-modeling`, `tdd`) are discovered and can be named. When the delivered procedure names a skill the kind cannot load (such as `/code-review`), a profile block names the substitute: re-read your own diff as a reviewer would before you land, and fix what you would flag.
- **codex**: unverified. Before its first dispatch, start one by hand with `-m <model> -c model_reasoning_effort=<effort>` and record here how it takes a skill file and what its crashed and exhausted tails look like.
