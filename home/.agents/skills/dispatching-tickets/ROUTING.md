# Routing a ticket

Every ticket runs on the profile that suits its **type**, and every subscription carries a share of the week. `estate.toml`'s `[routing]` table is the source of truth for both; this file explains how to use it. An estate with no `[routing]` table runs everything on `agents.active`, and the rest of this file does not apply.

## 1. Classify

Read the ticket and pick exactly one type from `routing.types`. Write it at the top of `head.md` as `Type: <type>`, so a handover routes the same way.

| Type | The ticket is | Skills the head names |
|---|---|---|
| `design` | an interface, module boundary, ADR or domain-language decision | `codebase-design`, `domain-modeling`, `grilling` |
| `spec-build` | a whole spec to build on an integration branch | `implement-spec` |
| `security` | code that handles keys, consent, the sandbox, IAM or private data | `implement`, `tdd` at every seam |
| `research` | a question answered from primary sources, with no code change | `research` |
| `implementation` | a specified feature, bugfix, refactor or test | `implement`, `tdd` |
| `debugging` | a failure whose cause is unknown | `diagnosing-bugs`, then `implement` |
| `prototype` | a design question best answered by something throwaway | `prototype` |
| `mechanical` | an edit with no judgement in it: renames, pins, boilerplate | `implement` |
| `human-steps` | work only the owner can finish: MFA, consoles, credentials | `wizard` |

When two types fit, take the one higher in this table: a security bugfix is `security`, a design question settled by a prototype is `design`. Shaping (`grill-with-docs`, `to-spec`, `to-tickets`) and triage run with the owner, outside the fleet: a ticket that needs shaping is surfaced, not dispatched.

## 2. Choose the profile

```
python3 ~/.agents/skills/dispatching-tickets/scripts/route.py <estate> <type>
```

It prints the profile, its provider, the agent suffix, and the full argument list for `herdr agent start ... --`, including the type's skill file. A fixed type takes its first non-exhausted profile. A fungible type takes the profile whose provider is furthest behind its quota **pace**: the share of its quota period elapsed minus the share of its allowance used, less `routing.agent_weight` for each agent already live on it, so one tick's tickets spread across providers. A provider well behind pace gets the next fungible ticket, which is how the subscriptions end the week evenly used. Write `Profile: <profile> (<reason from route.py>)` under the type in `head.md`.

A nonzero exit is the answer for this ticket this tick: no candidate is left, or the estate is inconsistent. Report it and dispatch the rest.

## 3. Keep the ledger

`<estate>/quota.toml` holds one reading per provider. At the start of every tick run `python3 scripts/quota_read.py <estate>`: it reads the Claude and ChatGPT weekly windows from the providers' own usage endpoints, with the logins Claude Code and pi already hold here, and rewrites those readings. A nonzero exit names the provider it could not read and why (an expired login renews when a session of that harness opens); report it and route on. Grok has no reader, so its reading is taken by hand as the file's header says; when it is older than `routing.stale_hours`, report it so the owner can refresh it. A reading is only ever what the provider itself reports; a missing or stale one counts as on pace, so a guess never steers the fleet. Report a provider more than 0.25 behind pace at the end of the week's second-to-last day, so the owner can point more fungible work at it.

## 4. Review across providers

`implementation`, `debugging`, `mechanical`, `spec-build` and `security` tickets merge only after a reviewer on another provider approves. The author reports `Ready for review: <PR>` and stops (the closing mandate tells it to). When a tail says so:

1. **Route**: `route.py <estate> review --author <author's provider>`, or `security-review` for a `security` ticket or a diff touching the paths `blocks/review.md` lists.
2. **Brief**: write `tickets/<n>/review-head.md`: the ticket, the PR link, the author's provider, and the verification the ticket names. Assemble it with `prompt.py <estate> <n> --review --profile <profile>`.
3. **Start** the reviewer in a new tab of the ticket's workspace, named `<agent_prefix><n>r<suffix>`, and submit `tickets/<n>/review.prompt.md`. The reviewer only reads the worktree; the author stays its one writer.
4. **Relay**: when the reviewer reports its verdict, tell the author to read the review on the PR. On changes requested, the author fixes and reports `Ready for re-review`; prompt the same reviewer to re-review. On approve, close the reviewer's tab and tell the author to merge.

A reviewer and an author that disagree twice on the same finding go to the owner, quoted side by side.

## 5. Escalate on evidence

Raise a ticket to a stronger profile only on evidence: a failed first pass, a second failed fix for the same symptom, or a root cause still unknown after a full `diagnosing-bugs` loop. Escalation is a handover per [RECOVERY.md](RECOVERY.md) to the next profile in the type's list, or to `claude-opus-high`, `gpt-astra` or `claude-fable`. Record the evidence in `head.md` so the new agent starts from it.
