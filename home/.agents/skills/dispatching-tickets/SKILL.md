---
name: dispatching-tickets
description: Run one tick of a dependency-driven dispatch loop over an estate - compute the ticket frontier, give each ready ticket its own agent and worktree on the profile its type routes to, run cross-provider reviews, recover crashed agents, retire finished sessions, report what changed.
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

**The estate directory is the only source of truth for how the fleet runs.** Read `estate.toml` and `blocks/` at the start of every tick. Your conversation memory and the loop's wakeup prompt are caches, and caches go stale: the fleet once kept a wakeup prompt naming a provider it had abandoned hours earlier. When the user changes how the fleet runs, write the change into the estate first, then act on it. A decision or approval the user gives about one ticket goes to its live agent as a prompt and into `tickets/<n>/head.md`, so every later handover carries it.

## Machine routing

For dispatcher placement, macOS-only tickets, or workers on another machine, read [MACHINES.md](MACHINES.md). It documents the VM-to-Mac Herdr connection and the manual ownership guard required because the tick scripts are local-only. Read it before computing a tick that includes remote-owned tickets.

## Fail closed

Every question a tick asks has one dangerous wrong answer: "not blocked", "nothing is running", "already landed". Unknown always resolves to the safe side: blocked, busy, unfinished. The scripts raise, quarantine or keep rather than guess, and print why. Take a refusal as this tick's answer, report it, and let the next tick retry.

## 1. Compute

```
python3 ~/.agents/skills/dispatching-tickets/scripts/frontier.py <estate> --record
```

`--record` saves this tick as the next tick's baseline, so run it with `--record` once per tick; ad hoc re-runs omit it. Nonzero exit means the tracker, the agent list or the worktrees could not be read: dispatch nothing this tick. Tickets under `unreadable` get no action this tick either; one still unreadable after several ticks goes to the user with its error.

## 2. Decide

Act on the `action` of every frontier ticket, and of every blocked ticket that carries one (its agent was dispatched before the blocker was added):

- **`leave`**: an agent is working.
- **`dispatch`**: carries the dispatch label and has no agent and no worktree. Dispatch it in step 3.
- **`orphaned`**: carries the dispatch label and has no agent, but a worktree carries its number: an agent died, its tab closed, or its start failed. The work is on disk; hand it over per [RECOVERY.md](RECOVERY.md).
- **`surface`**: carries the surface label. Tell the user what the human has to do; agents cannot do these (an MFA prompt fails in an agent shell).
- **`hold`**: needs triage, unlabeled, or both labels. Leave it; mention it only when it is new.
- **`read`**: its agents exist and none is working. herdr's status lags, so the **tail** decides, not the status: `herdr agent read <name> --source recent-unwrapped --lines 150`. Then classify:
  - **Crashed**: the tail ends in a `crashed` marker of the agent's profile. The provider dropped the turn; the work did not fail. Resume per [RECOVERY.md](RECOVERY.md).
  - **Exhausted**: the tail ends in an `exhausted` marker, or a resume came back refused for quota. Migrate per [RECOVERY.md](RECOVERY.md).
  - **Stopped**: the agent is waiting on a question or an approval. Answer it when the estate already holds the answer; surface it to the user verbatim when it is theirs.
  - **Ready for review**: an author reports `Ready for review` or `Ready for re-review`, or a reviewer reports its verdict. Run that step of the review per [ROUTING.md](ROUTING.md).
  - **Working after all**: the tail is still moving. Leave it.
  - **An error no marker lists**: decide from its text whether the provider or the work failed. A provider error goes into the profile's `crashed` or `exhausted` list verbatim, then is handled as that.

A crashed agent is resumed in place, never re-dispatched from scratch, and its worktree is the agent's alone: read it, never write it.

## 3. Dispatch

Refresh stale `quota.toml` readings first. Then each `dispatch` ticket, classified and routed per [ROUTING.md](ROUTING.md) and started per [DISPATCH.md](DISPATCH.md). Done when every new agent shows `working` with its prompt submitted, on the profile `route.py` chose.

## 4. Retire

After dispatch, never before: a worktree created this tick is protected only by the agent about to start in it.

```
python3 ~/.agents/skills/dispatching-tickets/scripts/cleanup.py <estate>            # report
python3 ~/.agents/skills/dispatching-tickets/scripts/cleanup.py <estate> --apply    # archive each report, close the tab, remove the worktree
```

A session is finished when its **ticket is closed and its branch has landed**. The script archives each tail into `tickets/<n>/` before it closes a tab, and keeps anything held by a live agent for that ticket number in any repository, dirty, unlanded, or whose ticket is not known to be closed. Its comments say why each guard exists. Report a surprising `kept` reason; never work around one.

## 5. Report

Report from `changes`. For each closed ticket, read what its agent reported, from the `tickets/<n>/report-*` that retire archived, or from the tail of a tab it kept: the terminal summary carries findings the closing comment omits. Report what closed and what it found, what was dispatched (with type and profile), reviewed, resumed or migrated, what newly waits on the human, what is unreadable, and each provider's pace. When nothing changed, report nothing.

## 6. Schedule the next tick

Under `/loop`, schedule the next wakeup `cadence_seconds` out with exactly this prompt, which names paths and nothing that can go stale:

```
Run one dispatch tick: follow ~/.agents/skills/dispatching-tickets/SKILL.md for the estate at <absolute estate path>.
```

## Shell

The operator's shell may be zsh, which does not word-split unquoted variables: `for x in $LIST` and `set -- $pair` each silently yield one item. Loop in Python, write the items literally (`for n in 110 111 112`), or split explicitly with `${=LIST}`.
