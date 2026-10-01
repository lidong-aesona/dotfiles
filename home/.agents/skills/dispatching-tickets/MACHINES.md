# VM and Mac coordination

## Placement and connectivity

Run the primary dispatcher and ordinary workers on `agent-vm-w1`. Use the Mac for macOS-only tasks; it must be awake and reachable over Tailscale.

The VM has a saved Herdr machine named `mac`, targeting SSH alias `herdr-mac` and the Mac's `default` session. Inspect current configuration with `herdr machine list --json` and `ssh -G herdr-mac`; `~/.ssh/config` is authoritative for connection details. The dedicated VM key is `~/.ssh/herdr_mac`; its public key is authorized on the Mac only from the VM's Tailscale IP. Keep the private key on the VM.

From a Herdr-managed VM pane (`HERDR_ENV=1`):

```bash
herdr --machine mac workspace list
herdr --machine mac agent list
herdr --machine mac agent read <name> --source recent-unwrapped --lines 150
herdr --machine mac agent prompt <name> '<task>'
```

Load the Herdr skill before controlling workers. Discover remote IDs and command syntax rather than reusing VM IDs. Scope every remote operation with `--machine mac`; a bare `herdr` command targets the caller's session. Machine connections are directional: the Mac's saved VM connection does not create the reverse connection.

## Dispatch safety

The current frontier, recovery, and cleanup scripts are local-session/local-filesystem operations, not a multi-machine scheduler. Remote access alone does not make Mac workers visible to those scripts.

Before assigning a ticket to a Mac worker, record its machine, session, agent, worktree path, and handoff state in the estate's `tickets/<n>/head.md`. Persist the routing policy in the estate as well. Keep the ticket out of automatic local dispatch while remote-owned using the estate's existing hold/label convention; verify the computed frontier cannot dispatch a duplicate. If no safe exclusion exists, surface the ticket instead of launching it. Inspect and retire Mac work manually through the remote connection; VM cleanup cannot manage Mac worktrees. Resume local ownership only after an explicit recorded handoff.

If the Mac is unavailable, leave its tickets blocked and continue independent VM work. Never interpret a failed remote query as an empty worker list.
