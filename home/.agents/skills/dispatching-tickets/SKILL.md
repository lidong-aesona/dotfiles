---
name: dispatching-tickets
description: Run one tick of a dependency-driven dispatch loop over an estate - compute the ticket frontier, give each ready ticket its own agent and worktree on the profile its type routes to, run the second reviews the estate calls for, recover crashed agents, retire finished sessions, report what changed.
disable-model-invocation: true
argument-hint: "An estate directory, or nothing to set one up"
---

# Dispatching tickets

A **tick** turns an issue tracker's dependency graph into running agents: every ticket whose blockers are all closed gets one agent, in one git worktree, on one branch, with the goal of closing the ticket. Each agent runs on the harness, model and effort its ticket's type routes to, and fungible work flows to whichever subscription is furthest behind its pace, so every subscription carries its share of the week. Ticks repeat until the tracker is empty or the user stops them.

Needs: tickets as GitHub issues (`gh`), agents in herdr (`HERDR_ENV=1`), Python 3.11+.

## The estate

The **estate** is everything one fleet runs against: the tracker, the repositories and their base branches, the label vocabulary, the agent profiles, and the prose blocks pasted into every dispatch. It lives in one **estate directory** outside every repository:

```
<estate>/estate.toml          configuration; its comments document every key
<estate>/blocks/*.md          prose appended to every dispatch prompt
<estate>/tickets/<n>/         head.md, assembled prompts, resumes, handovers, archived reports
<estate>/last-tick.json       the baseline "what changed" is computed against
<estate>/quota.toml           one usage reading per provider, for routing
```

No estate directory was given: follow [SETUP.md](SETUP.md), then stop.

**The estate directory is the only source of truth for how the fleet runs.** Read `estate.toml` and `blocks/` at the start of every tick. Your conversation memory and the loop's wakeup prompt are caches, and caches go stale: the fleet once kept a wakeup prompt naming a provider it had abandoned hours earlier. When the user gives a decision, persist it before acknowledging or acting: fleet settings, holds and freezes go to `estate.toml`, shared instructions to `blocks/`, and ticket decisions/approvals (who, when, exact scope and conditions) to `tickets/<n>/head.md`. Relay ticket decisions to the live worker after recording them. The small owner-chat session follows this same write-first rule; it is not a second dispatcher. A fresh tick starts from those files, not an operator tail or conversation summary.

## Machine routing

For dispatcher placement, macOS-only tickets, or workers on another machine, read [MACHINES.md](MACHINES.md). It documents the VM-to-Mac Herdr connection, estate label-to-machine routing and shared ownership guards. Read it before computing a tick that includes remote-owned tickets.

## Fail closed

Every question a tick asks has one dangerous wrong answer: "not blocked", "nothing is running", "already landed". Unknown always resolves to the safe side: blocked, busy, unfinished. The scripts raise, quarantine or keep rather than guess, and print why. Take a refusal as this tick's answer, report it, and let the next tick retry.

## 1. Compute

```
python3 ~/.agents/skills/dispatching-tickets/scripts/frontier.py <estate> --record --brief
```

`--record` saves this tick as the next tick's baseline, so run it with `--record` once per tick; ad hoc re-runs omit it. `--brief` lists tickets nobody acts on this tick (hold, leave, unchanged) by number only, under `quiet_frontier` and `quiet_blocked`. The output also reports estate `holds` and `freeze`; read both, and include an active freeze in the tick report. Nonzero exit means invalid state or an unreadable tracker/baseline: dispatch nothing this tick. Machine read failures instead appear under `machine_errors`; their routed and last-known owned tickets get `unknown`, while independent readable-machine work proceeds. Tickets under `unreadable` get no action this tick either; one still unreadable after several ticks goes to the user with its error.

## 2. Decide

Act on the `action` of every frontier ticket, and of every blocked ticket that carries one (its agent was dispatched before the blocker was added):

- **`leave`**: an agent is working.
- **`dispatch`**: no agent or worktree and eligible on its machine. VM eligibility is `[labels].dispatch` (`fleet:w1`) plus `[labels].dispatch_requires` when set (currently `ready-for-agent`); an orphaned worktree is recovered without it. Mac eligibility requires `fleet:mac` plus `[machines.mac].dispatch_requires` (currently `ready-for-agent`); `ready-for-human` always surfaces on the Mac. A Mac ticket without its required label holds. Both fleet labels together hold as a conflict. Human-labelled Mac work is dispatched by hand, never automatically.
- **`unknown`**: its routed machine is unreadable, or the previous baseline recorded an agent/worktree on an unreadable machine. No dispatch, orphan recovery or retirement. Report `machine_errors`; continue independent VM work. Recorded ticks retain an `unknown_ownership` ledger until the machine is readable again.
- **`orphaned`**: carries the dispatch label and has no agent, but a worktree carries its number: an agent died, its tab closed, or its start failed. The work is on disk; hand it over per [RECOVERY.md](RECOVERY.md).
- **`surface`**: carries the surface label. Tell the user what the human has to do; agents cannot do these (an MFA prompt fails in an agent shell).
- **`hold`**: an explicit issue number in estate `holds`, a dual-fleet conflict, a Mac ticket lacking its configured required label (unless human-labelled, which surfaces), or the VM hold cases: needs triage, unlabeled, lacking `[labels].dispatch_requires`, or both VM dispatch/surface labels. The estate list wins over every label, agent and worktree, including on blocked tickets: no dispatch, resume, migration or automatic relabeling. A hold is not a process stop; relay a newly recorded hold to a live worker and surface any required stop to the owner. Mention a hold when it is new. Release it by removing its number from `holds` only on owner instruction, recording the release in its head.
- **`read`**: its agents exist and none is working. herdr's status lags, so the **tail** decides, not the status: `herdr agent read <name> --source recent-unwrapped --lines 150` locally; for a Mac agent use `dispatch.py <estate> <n> --read <name>` or `herdr --machine mac agent read <name> --source recent-unwrapped --lines 150`. Use `agent_machines` from the frontier, not the label alone. Then classify:
  - **Crashed**: the tail ends in a `crashed` marker of the agent's profile. The provider dropped the turn; the work did not fail. Resume per [RECOVERY.md](RECOVERY.md).
  - **Exhausted**: the tail ends in an `exhausted` marker, or a resume came back refused for quota. Migrate per [RECOVERY.md](RECOVERY.md).
  - **Stopped**: the agent is waiting on a question or an approval. Answer it when the estate already holds the answer; surface it to the user verbatim when it is theirs.
  - **Ready for review**: an author reports `Ready for review` or `Ready for re-review`, or a reviewer reports its verdict. Run that step of the review per [ROUTING.md](ROUTING.md).
  - **Working after all**: the tail is still moving. Leave it.
  - **An error no marker lists**: decide from its text whether the provider or the work failed. A provider error goes into the profile's `crashed` or `exhausted` list verbatim, then is handled as that.

A crashed agent is resumed in place, never re-dispatched from scratch, and its worktree is the agent's alone: read it, never write it.

### Merge freeze preflight

Immediately before **every merge**, by the dispatcher or by an author whose estate has it merge its own PR, run:

```
python3 ~/.agents/skills/dispatching-tickets/scripts/freeze.py <estate> --repo <repo-slug> --pr <n> --head <full-head-sha>
```

Proceed only on exit 0, and merge only that SHA with `gh pr merge --merge --match-head-commit <full-head-sha>`. A frozen path, unreadable/truncated file list or moving head/base refuses the merge; report it and leave the PR open. Rename sources and deletions count. This preflight grants no review, gate, research approval or live permission and replaces none of them. Re-read estate state for each merge: `until` is a review condition, never automatic expiry. Only owner confirmation releases a freeze by removing its table.

## 3. Dispatch

Refresh `quota.toml` first with `scripts/quota_read.py` ([ROUTING.md](ROUTING.md) section 3). Then each `dispatch` ticket, classified and routed per [ROUTING.md](ROUTING.md) and started per [DISPATCH.md](DISPATCH.md). For a remote row use DISPATCH.md's Mac command and its estate machine profile instead of local start commands. Done when every new agent shows `working` with its prompt submitted on its routed machine and profile.

## 4. Retire

After dispatch, never before: a worktree created this tick is protected only by the agent about to start in it.

```
python3 ~/.agents/skills/dispatching-tickets/scripts/cleanup.py <estate>            # report
python3 ~/.agents/skills/dispatching-tickets/scripts/cleanup.py <estate> --apply    # archive each report, close the tab, remove the worktree
```

A session is finished when its **ticket is closed and its branch has landed**. The script reads all configured machines, checks clean/landed work before closing a tab, archives each quiet tail into `tickets/<n>/report-<agent>.md`, then removes its worktree on that machine. It keeps anything held by a live agent for that ticket number on any machine or repository, dirty, unlanded, or whose ticket is not known to be closed. Its comments say why each guard exists. Report a surprising `kept` reason; never work around one.

## 5. Report

Report from `changes`. For each closed ticket, read what its agent reported, from the `tickets/<n>/report-*` that retire archived, or from the tail of a tab it kept: the terminal summary carries findings the closing comment omits. Lead with **Your actions**: what waits on the owner, ranked by how much work each unblocks, each with its link, the exact step and what it unblocks. Then each breakdown approved since the last report, with its epic, tier and sub-tickets ([ROUTING.md](ROUTING.md) section 6), and any `spec-approved` epic whose sub-issues are all closed but that is still open. Then report what closed and what it found, what was dispatched (with type and profile), reviewed, resumed or migrated, what is unreadable, and each provider's pace. When nothing changed and no freeze is active, report nothing. The owner-chat session answers a status question in the same order: Your actions first, then one line per live session (owner, 2026-10-26). With an active freeze, report its repositories, paths, reason and release condition.

## 6. Finish the tick

In a fresh runner session, run exactly one tick and exit. Its scheduler launches a new process/session for each tick; the tick does not install a loop or schedule another wakeup. Until the owner switches over, the legacy operator keeps its existing schedule and returns to chat after the tick; editing this skill does not authorize replacing that schedule. Use exactly this prompt, which names paths and nothing that can go stale:

```
Run one dispatch tick: follow ~/.agents/skills/dispatching-tickets/SKILL.md for the estate at <absolute estate path>.
```

The runner is `scripts/tick.sh <estate>`: it starts in the estate, with a new UUID and no resume/continue, reads the prompt above, and saves stream usage under `<estate>/ticks/`. Safe mode excludes auto-memory, repository instructions, hooks and plugins at startup; the tick explicitly reads this skill and estate files, then queries tracker/worker state as needed. This is context isolation, not a filesystem sandbox. An estate lock skips overlapping runner invocations; an external timeout bounds each run. For the disabled w1 timer, owner-only switch and rollback, and one-day measurement commands, read `<estate>/tickets/1097/RUNNER.md`. Keep the old dispatcher running until the owner performs the switch.

## Shell

The operator's shell may be zsh, which does not word-split unquoted variables: `for x in $LIST` and `set -- $pair` each silently yield one item. Loop in Python, write the items literally (`for n in 110 111 112`), or split explicitly with `${=LIST}`.
