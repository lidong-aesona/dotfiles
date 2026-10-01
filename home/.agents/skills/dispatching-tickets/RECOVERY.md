# Recovering agents

Two failures look alike in a tail and need opposite responses:

- A **crash** is a dropped turn. The agent process is alive at its prompt with its context intact, so resume it.
- **Exhaustion** is the provider refusing for hours or days. The agent cannot take another turn, so its work moves to a new agent on another profile, in the same worktree, with the work on disk intact.

A handover also serves **escalation**: a ticket that needs a stronger profile, on the evidence ROUTING.md names, moves the same way.

Scripts are in `~/.agents/skills/dispatching-tickets/scripts/`.

## Resume a crashed agent

1. **Measure**, read-only, in each of the ticket's worktrees: `git status --short` and `git log --oneline origin/<base>..HEAD`. Read the ticket's comments since dispatch.
2. **Write** `<estate>/tickets/<n>/resume-<k>.md`:
   - The last turn was killed by transport (quote the marker). Nothing is wrong with the work and the ticket has not changed. Resume.
   - What you measured: commits, uncommitted files, and that you have not touched the worktree.
   - Commit work in progress at every seam that leaves the tree coherent, so a dropped turn costs minutes rather than an hour of reasoning.
   - Run any long transfer or job detached, with a log, and poll it, so a dropped turn costs a poll rather than the job.
   - The head's traps that are easiest to get backwards, restated: this agent was interrupted mid-thought.
   - Your goal is still to close the ticket.
3. **Submit**: `herdr agent prompt <name> "$(cat <estate>/tickets/<n>/resume-<k>.md)"`, then confirm from the tail that it is working.

An agent that crashes repeatedly is still resumed; the markers point at the provider, not the work. A resume refused for quota is exhaustion.

## Migrate an exhausted fleet

Exhaustion arrives all at once: every agent on the provider dies within minutes, whatever its profile, because they share one quota.

1. **Move the estate first.** On every profile of the exhausted provider, set `exhausted_until` to the reset time the provider gave, and set that provider's `used` to 1.0 in `quota.toml`. From here `route.py` skips those profiles, whatever any prompt or memory says. Choose each affected ticket's replacement with `route.py <estate> <its Type>`. When it refuses because a fixed type has nothing left, take the first `fallback` profile with nothing recorded against it, or, when `fallback` is empty, the user's answer to a question naming the reset time and the affected tickets. An estate with no `[routing]` sets `active` to the replacement instead.
2. **Hand over** every affected ticket, below, taking all of them through each step before starting the next.
3. **Report** which tickets moved, to which profile, and what each had left.

When the provider resets, clear `exhausted_until`. Live agents stay on the profile they run on; moving new dispatches back is the user's call.

## Hand over a ticket

For an exhausted agent, an escalation, and an `orphaned` ticket whose agent is already gone. The new agent runs on the profile `route.py` chooses for the ticket's `Type` (an escalation names its profile; an estate with no `[routing]` uses `active`), in the existing worktree, with the work on disk intact. Update the `Profile:` line in `head.md` to match.

1. **Archive** the old agent's tail when it still exists, the last record of its reasoning: `herdr agent read <name> --source recent-unwrapped --lines 400 > <estate>/tickets/<n>/tail-<name>.txt`. Also read any `report-*` or `tail-*` already archived there.
2. **Measure what it left**, read-only, in every worktree the ticket has: path, branch, base commit, `git status --short`, commits ahead of base, any pushed branch or open pull request. From the tail: changes made outside git (resources created, with their ids), approvals it was waiting on, questions it had asked.
3. **Write** `<estate>/tickets/<n>/handover.md` from that measurement, never from the old agent's plan:
   - You are taking over #<n> from an agent that stopped mid-task (say why: provider quota, a closed tab, an escalation and its evidence). Its reasoning is gone; its work on disk is not. Read the worktree before anything else.
   - The worktree path, branch and base commit, and the `git status` output verbatim.
   - Read every diff and decide whether you agree before building on it: you are responsible for what you land. Commit early once you agree.
   - Changes already made outside git, with ids.
   - Approvals the user granted since the old agent stopped, each with its exact scope and the condition it rests on, and a stop instruction for when that condition proves false. Beyond those, the standing stop rules hold.
   - When the old agent left nothing: a fresh start on a clean branch.
4. **Assemble**: `python3 ~/.agents/skills/dispatching-tickets/scripts/prompt.py <estate> <n> --preface tickets/<n>/handover.md --profile <profile>`. The handover is followed by the original dispatch, re-assembled from today's head and blocks and the new profile's blocks.
5. **Swap the agent**, keeping exactly one agent in the worktree:

   ```
   herdr tab create --workspace <workspace id> --cwd <worktree path> --label "<n> <kind>" --no-focus
   herdr tab close <old agent's tab id>        # when the old agent still exists
   herdr agent start <agent_prefix><n><suffix> --kind <kind> --pane <new pane> --timeout <timeout_ms> -- <args from route.py>
   herdr agent prompt <new name> "$(cat <estate>/tickets/<n>/handover.prompt.md)"
   ```

   The old tab closes before the new agent starts: a dead agent left beside its replacement is one mistaken resume away from two agents writing one worktree. Should the start then fail, the next tick sees the ticket as `orphaned` and hands it over again. A worktree with no open workspace is reopened with `herdr worktree open`. Apply DISPATCH.md's agent-kind notes to the start.
