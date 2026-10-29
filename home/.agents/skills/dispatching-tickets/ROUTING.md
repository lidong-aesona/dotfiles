# Routing a ticket

Every ticket runs on the profile that suits its **type**, and every subscription carries a share of the week. `estate.toml`'s `[routing]` table is the source of truth for both; this file explains how to use it. An estate with no `[routing]` table runs everything on `agents.active`, and the rest of this file does not apply.

## 1. Classify

Read the ticket and pick exactly one type from `routing.types`. Write it at the top of `head.md` as `Type: <type>`, so a handover routes the same way.

| Type | The ticket is | Skills the head names |
|---|---|---|
| `design` | an interface, module boundary, ADR or domain-language decision | `codebase-design`, `domain-modeling`, `grilling` |
| `spec-build` | a whole spec to build on an integration branch | `implement-spec` |
| `breakdown` | an approved epic spec to split into sub-tickets (section 6) | `to-tickets` |
| `security` | a change to a guard or grant: keys, consent, the sandbox, IAM, or where private data may flow. Showing or moving data inside an existing boundary is `implementation` | `implement`, `tdd` at every seam |
| `research` | a question answered from primary sources, with no code change | `research` |
| `implementation` | a specified feature, bugfix, refactor or test | `implement`, `tdd` |
| `debugging` | a failure whose cause is unknown | `diagnosing-bugs`, then `implement` |
| `prototype` | a design question best answered by something throwaway | `prototype` |
| `mechanical` | an edit with no judgement in it: renames, pins, boilerplate | `implement` |
| `human-steps` | work only the owner can finish: MFA, consoles, credentials | `wizard` |

When two types fit, take the one higher in this table: a security bugfix is `security`, a design question settled by a prototype is `design`. Grilling, `to-spec` and triage run with the owner, outside the fleet: a ticket that needs shaping is surfaced, not dispatched. Once the owner approves a spec, its split into tickets (`to-tickets`) runs in the fleet as a `breakdown` ticket (section 6).

## 2. Choose the profile

```
python3 ~/.agents/skills/dispatching-tickets/scripts/route.py <estate> <type>
```

It prints the profile, its provider, the agent suffix, and the full argument list for `hc run ... --`, including the type's skill file. A fixed type takes its first non-exhausted profile. A fungible type takes the profile whose provider is furthest behind its quota **pace**: the share of its quota period elapsed minus the share of its allowance used, less `routing.agent_weight` for each agent already live on it, so one tick's tickets spread across providers. A provider well behind pace gets the next fungible ticket, which is how the subscriptions end the week evenly used. Write `Profile: <profile> (<reason from route.py>)` under the type in `head.md`.

A nonzero exit is the answer for this ticket this tick: no candidate is left, or the estate is inconsistent. Report it and dispatch the rest.

## 3. Keep the ledger

`<estate>/quota.toml` holds one reading per provider. At the start of every tick run `python3 scripts/quota_read.py <estate>`: it reads the Claude and ChatGPT weekly windows from the providers' own usage endpoints, with the logins Claude Code and pi already hold here, and rewrites those readings. A nonzero exit names the provider it could not read and why (an expired login renews when a session of that harness opens); report it and route on. Grok has no reader, so its reading is taken by hand as the file's header says; when it is older than `routing.stale_hours`, report it so the owner can refresh it. A reading is only ever what the provider itself reports; a missing or stale one counts as on pace, so a guess never steers the fleet. Report a provider more than 0.25 behind pace at the end of the week's second-to-last day, so the owner can point more fungible work at it.

## 4. Second review on another provider

The estate's `blocks/closing-mandate.md` decides which pull requests get a second reviewer; read it there, not here. By default the author's own review is the review, and a second reviewer on another provider runs only for a `security`, `design` or `breakdown` ticket (a breakdown's review is of its sub-issues, section 6), or a diff touching the paths `blocks/review.md` lists. Docs-, tests- and evidence-only pull requests get no second reviewer, whatever the ticket's type: read the diff, and merge on green checks and the approvals the repository requires. Every author reports `Ready for review: <PR>` and stops. When a second review is due:

1. **Route**: `route.py <estate> security-review --author <author's provider>` for a `security` ticket or a security-path diff, `review` for a `design` ticket, or `breakdown-review` for a `breakdown` ticket.
2. **Brief**: write `tickets/<n>/review-head.md`: the ticket, the PR link (for a breakdown, the epic, its sub-issues and the breakdown comment, with section 6's checks), the author's provider, and the verification the ticket names. Assemble it with `prompt.py <estate> <n> --review --profile <profile>`.
3. **Start** the reviewer in the worktree, in the ticket's space (`--label herdr.space=ticket-<n>`, DISPATCH.md section 6), named `<agent_prefix><n>r<suffix>`, and submit `tickets/<n>/review.prompt.md`. The reviewer only reads the worktree; the author stays its one writer.
4. **Relay**: when the reviewer reports its verdict, tell the author to read the review on the PR. On changes requested, the author fixes and reports `Ready for re-review`; prompt the same reviewer to re-review. On approve, remove the reviewer (`hc kill <name> && hc rm <name>`) and tell the author to merge.

A reviewer and an author that disagree twice on the same finding go to the owner, quoted side by side.

## 5. Escalate on evidence

Raise a ticket to a stronger profile only on evidence: a failed first pass, a second failed fix for the same symptom, or a root cause still unknown after a full `diagnosing-bugs` loop. Escalation is a handover per [RECOVERY.md](RECOVERY.md) to the next profile in the type's list, or to `claude-opus-high`, `gpt-astra` or `claude-fable`. Record the evidence in `head.md` so the new agent starts from it.

## 6. Breakdown of an approved spec

The owner approves each epic spec (owner, 2026-10-27, software factory decisions in the estate log). The fleet turns it into dispatchable tickets; the owner sees the set in the next status report.

1. **Approve**: before asking, the dispatcher checks the spec against `GLOSSARY.md`, `docs/adr/` and the current code and lists conflicts in the ask. On the owner's "approve" in chat, it adds the estate's `labels.spec_approved` label to the epic, comments the approval (who, when, quoted words) and logs it in the estate. A spec that weakens a guard, widens a grant or changes lease opening needs the owner's signed row instead.
2. **File the breakdown ticket** in the epic's repository: `Break down <repo>#<epic>`, labelled `fleet:w1` and `ready-for-agent`, with `tickets/<key>/head.md` opening `Type: breakdown` and naming the epic. The tick dispatches it like any other.
3. **The breakdown session** reads the epic body (the spec) and comments, explores the code, and picks a tier: (a) one ticket; (b) a graph of 5 or fewer tickets in one repository with no `security`, `design` or `human-steps` ticket and no live proof or deploy between tickets; (c) everything else. Until `spec-build` sessions exist, it files (b) like (c), `fleet:w1` included, and names the fit in its report; this is an interim deviation from the owner's tier (b) rule that its sub-tickets carry no `fleet:w1` (Q15). It files every ticket as a native sub-issue of the epic, with acceptance criteria, `Blocked by` bullets, a `Type:` line and `fleet:w1`, and never `ready-for-agent`. It comments on the breakdown ticket: the tier, a table mapping each spec requirement to the criterion that covers it, and the files each ticket expects to touch. It reports `Ready for review: breakdown <key>` and stops.
4. **Review** per section 4, with `route.py <estate> breakdown-review --author <author's provider>`. `review-head.md` names the epic, the breakdown ticket and its comment, and these checks: every spec requirement is covered by a ticket criterion; the blocking edges are complete and acyclic; no two unordered tickets edit the same file; the tier fits. The reviewer may raise the tier, never lower it. It posts its verdict on the breakdown ticket. On approve, it adds `ready-for-agent` to every sub-issue (the estate's `labels.dispatch_requires` gate), and the dispatcher tells the author to close the breakdown ticket.
5. **Close the epic**: the worker that closes an epic's last open sub-issue runs `scripts/metrics.py <estate> <repo> <epic> --append`, then closes the epic with that row as its comment.
