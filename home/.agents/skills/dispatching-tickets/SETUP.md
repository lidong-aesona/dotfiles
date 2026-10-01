# Setting up an estate

Build the estate directory with the user. The first tick runs when the user starts the loop.

## 1. Explore, read-only

- **The tracker**: its labels (`gh label list --repo <tracker>`), and the tracker repository's `docs/agents/triage-labels.md` when it exists.
- **How blockers are written**: a few issue bodies, and a native link or two (`gh api repos/<tracker>/issues/<n>/dependencies/blocked_by`). `frontier.py` reads native links, a `Blocked by` heading's bullets, and `Blocked by: #12, #14` lines. It treats a bullet whose leading word is None or Nothing as no blocker, a struck-through first reference as resolved, and every other reference by its live state. A tracker that declares blockers any other way reads as unblocked until `frontier.py` is taught that way, so settle this before the first tick.
- **The repositories** agents will write to: primary checkout path, the base branch new work starts from, the branches work lands in, and long-lived worktrees to keep (`herdr worktree list --cwd <path>`).
- **Profiles and routing**: which subscriptions the user has, which harnesses reach each one on this machine (`pi auth`, `claude`, `codex`), and the model and effort for each profile. Start from the template's `[routing]` table and confirm each type's profiles with the user. Read [ROUTING.md](ROUTING.md) first.
- **Quota readings**: how each provider's usage is read on this machine. Take one reading per provider into `quota.toml` before the first tick.
- **Existing material**: a runbook, prompts or scratchpad from a hand-run fleet is the best source for the blocks. Carry every rule across; drop the history.

## 2. Choose the directory

Durable, and outside every repository: default `~/.agents/estates/<name>/`. Keep it out of a session scratchpad, which dies with the session and cannot be found by the next operator.

## 3. Draft

`estate.toml` from `templates/estate.toml`; `quota.toml` from `templates/quota.toml`; `blocks/` from `templates/blocks/`. Each `<!-- ESTATE -->` comment is a question for exploration or the user: replace it with the answer. A single-repository estate drops `repo-boundary.md` from the files and from `prompt.blocks`.

## 4. Confirm with the user

Show the drafts. Confirm the labels, the stop rules in `closing-mandate.md`, the routing table, and `fallback` explicitly: those decide what agents do without asking. Write once confirmed.

## 5. Check

Run `frontier.py <estate>` without `--record`, `cleanup.py <estate>` without `--apply`, and `route.py <estate> <type>` for every type. Setup is done when both run cleanly and the user agrees with what they report. An existing fleet's agents are invisible to both until their names match `<agent_prefix><n>`; rename them with `herdr agent rename`.

Then hand the user the command that starts ticking:

```
/loop Run one dispatch tick: follow ~/.agents/skills/dispatching-tickets/SKILL.md for the estate at <absolute estate path>.
```
