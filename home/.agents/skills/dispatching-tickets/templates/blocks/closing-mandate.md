Your goal is to close the ticket

- **The job is done when the ticket is closed.** Work it through to completion: build it, prove each acceptance criterion with a real call rather than an assertion, land the work, and close the issue.
- Land it the way this estate does: <!-- ESTATE: e.g. commit on your branch, push, open a PR in the repository you are working in, merge it when <the gate> passes, and record the evidence as a comment on the ticket. Name the gate: CI, or a local command when there is no CI. -->
- **Review routing:** your own review is the review. When the pull request is green and your review is done, report `Ready for review: <PR link>` and stop. A second reviewer, on another provider, runs only for a `security` or `design` ticket or a diff touching <!-- ESTATE: the security paths, as in blocks/review.md -->. Address its blocking findings, push, and report `Ready for re-review`; one round. Docs-, tests- or evidence-only pull requests skip the code review.
- **Tick a criterion only when you have actually met it.** A criterion you cannot run because a prerequisite does not exist graduates to a successor ticket, blocked by that prerequisite, named in your closing comment. <!-- ESTATE: cite one or two real graduations from this tracker as the pattern to copy. -->
- **Breakage you meet along the way lands in this PR (owner, 2026-10-26).** Fix a small pre-existing break you find (a red test, a lint error, a stale doc line) in this PR. Put larger ones together in one successor ticket for the set, named in your closing comment. A successor ticket carries a criterion this ticket could not meet or a harm with a stated cause; ideas and nice-to-haves go in the closing comment.
- **Before you close, edit the issue body so every box reads true.** Tick what you met. Rewrite each line you did not meet, in place, to say why and where it went: `- [ ] **Implemented; live proof graduated to #N:** ...`. A closed ticket with bare unticked boxes reads as abandoned work. The body is the record; a closing comment does not substitute for it.
- Close only when every criterion is met or graduated. While a criterion is unmet and nothing stops you from meeting it, keep working.
- Wire dependencies for the successor you create and leave every other ticket's dependencies as they are.

What still stops you and makes you ask

<!-- ESTATE: the few acts that need a human first, each with its reason. Keep this list short
enough that every item is a real stop: data the agents must never touch, lockout-class or
irreversible acts. Say explicitly that anything the ticket's own criteria call for is allowed. -->
