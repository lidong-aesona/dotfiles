@~/.agents/AGENTS.md

## Compact instructions

Write the summary so the next agent can continue the current work without
repeating completed actions or asking again for information and authorization
already provided. Prioritize information needed for the next concrete action.

Preserve, in this order:

- The active goal, completion criteria, and latest user corrections. Distinguish
  follow-up requests from an explicit change of objective.
- Authorization already granted, its scope, pending decisions, and
  responsibilities assigned to the user or other agents. Quote wording where it
  determines scope.
- Relevant repositories, checkout paths, branches, issue and PR links, and
  authoritative spec or handoff references. Identify this session's changes and
  changes belonging to another session.
- Decisions and their reasons. Keep rejected approaches when their reason will
  prevent repeating a mistake.
- Completed work, remaining work, blockers, and the next concrete action.
  Distinguish implemented, committed, deployed, and verified states.
- Verification evidence: relevant repro and check commands, latest results,
  revision and environment, and remaining gaps. Distinguish mocked tests, dry
  runs, and live end-to-end verification. Keep exact error lines needed to
  recognize or diagnose an unresolved failure.
- Ongoing agents, commands, CI checks, and cloud operations: identifiers, last
  observed status and time, log or result locations, and pending follow-up or
  cleanup.
- Skills explicitly requested by the user, and any skill needed next with its
  purpose.

Reconcile earlier summaries with newer evidence. Replace superseded facts,
retain unresolved obligations, and mark uncertainty explicitly. Reference
existing specs, plans, ADRs, issues, commits, and diffs by path or URL, naming the
relevant section and why it matters. Preserve conversational facts those
artifacts cannot recover. Drop repeated file contents, routine successful
output, and obsolete plans. Omit secret values; retain references to their
configured location. Recheck changing external state before acting on it.

## Session notes

For multi-step work, maintain concise notes at the exact session-specific path
provided by the SessionStart hook. Each session has its own file outside project
checkouts; use only the current session's path. Skip notes for quick single-step
work or when no notes path was provided.

- Update notes at meaningful milestones: a decision or correction, a completed
  subtask, a change of direction, or a discovered blocker. Refresh them before a
  deliberate handoff or manual compaction.
- Keep the active goal and next action first, followed by constraints and
  authorization, decisions with reasons, verification, ongoing operations, and
  references as needed. Aim for at most 60 lines and 8,000 characters; the hook
  bounds the total injected context to 9,000 characters.
- Replace obsolete entries. When switching to unrelated work within a session,
  replace its notes with the new task's state.
- Treat restored notes as a checkpoint. Newer user instructions and verified
  evidence take precedence. The hook reloads saved notes; it does not update them.
- Keep runtime notes on the current machine, outside git. Use a deliberate
  handoff when transferring work to another session or machine.
