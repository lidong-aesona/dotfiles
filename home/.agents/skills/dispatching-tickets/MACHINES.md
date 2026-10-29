# VM and Mac coordination

## Placement and connectivity

Run the primary dispatcher and ordinary workers on `agent-vm-w1`. Use the Mac for macOS-only tasks; it must be awake and reachable over Tailscale.

The VM's hc has a machine named `mac`, reached over SSH alias `herdr-mac`. Inspect it with `hc machine ls` and `ssh -G herdr-mac`; `~/.ssh/config` is authoritative for connection details. The dedicated VM key is `~/.ssh/herdr_mac`; its public key is authorized on the Mac only from the VM's Tailscale IP. Keep the private key on the VM.

From the VM:

```bash
hc ls --json                              # VM and Mac terminals; Mac addresses are mac/<id>
hc read mac/<name> --lines 150
hc send mac/<name> '<task>'
hc run --on mac --bg --name <name> --cwd <path> ... -- zsh -lic 'exec <agent> ...'
```

Address every Mac terminal as `mac/<name>` or `mac/<id>`; a bare name targets the VM. `hc ls --json` lists `machines` with their `state`; a Mac that is not `connected` is unreadable, never empty. Mac agents start through `zsh -lic`, because only a login interactive shell puts Homebrew's `pi` and `claude` on PATH.

## Dispatch safety

One VM dispatcher owns both machines. `estate.toml` maps labels to machines in `[machine_labels]`: `fleet:mac` targets `mac`, `fleet:w1` targets local (`""`). A ticket carrying both fleet labels holds as a conflict without refusing independent tickets. `[watcher].machines` lists every machine whose ownership must be read, even when the watcher service is disabled. `[machines.mac]` defines SSH alias, home, profile, agent prefix, skill and gate-lock instruction; its `repos` table maps repository names to absolute Mac paths. Keep one dispatcher, not one per machine.

`frontier.py` reads agents and each repository's worktrees on every configured machine. An agent or worktree carrying a ticket number anywhere protects it from fresh dispatch everywhere. Machine IDs accompany agent/worktree ownership in the frontier. Unreadable JSON, failed SSH/hc calls and timeouts mean unknown/busy, never empty. Refusal is per machine: its routed tickets and tickets with last-known ownership there get action `unknown`, never dispatch/orphan/retire. `machine_errors` reports the failed reads. The baseline records complete per-machine `ownership`, including closed-ticket trees and agents' numbered cwd paths outside the open report rows. Its `unknown_ownership` ledger retains those historical owners across sleeping ticks and ticket closure until that machine is readable again. Independent VM dispatch and retirement continue. VM eligibility is `[labels].dispatch = "fleet:w1"` with the existing VM label precedence, plus `[labels].dispatch_requires` (currently `ready-for-agent`) for a fresh dispatch; orphan recovery skips that gate. Mac-routed work requires `[machines.mac].dispatch_requires`, currently `ready-for-agent`; `ready-for-human` always surfaces on the Mac (even with the required label), and a Mac ticket without the required label holds. Human-labelled Mac work is dispatched by hand, not by the tick.

For Mac dispatch use `dispatch.py` as described in DISPATCH.md. It rechecks global ownership and dependencies, assembles the usual head and blocks with a Mac preamble, fetches the remote base, creates and verifies a worktree, records ownership in `head.md`, starts the configured pi profile and sends the prompt through `hc send`. Failed starts or submissions leave ownership visible for recovery; never retry a fresh create over that worktree.

`cleanup.py` reads both machines and retires closed, clean, landed work only after its agents are quiet and archived. When an agent name and cwd carry different tickets, every owned ticket and worktree must pass the guards; its tail is archived into every owned ticket directory before closing. Cleanup also reads ticket labels so Mac-routed local leftovers remain protected during a Mac outage. Failures arising later during git checks, tail archival, agent rechecks or removal remain scoped to that machine and quarantine its routed/historical owners without stopping independent VM retirement. Mac git checks run over `ssh -n herdr-mac` in a login zsh; hc reads and removals address `mac/<id>`. Never reuse VM terminal IDs on the Mac.

## Unreachable-Mac fallback

Leave ownership and labels unchanged, report unknown/busy and retry connectivity next tick. If automation is unavailable but a separate connection to the Mac works, the dispatcher may perform DISPATCH.md/RECOVERY.md's steps manually through that connection: first read both machines, persist machine/agent/worktree in the head, send the prompt file's contents, and scope all remote commands explicitly. If neither path can read Mac ownership, leave its routed and historical owners unknown: no launch, VM handoff or retirement for those tickets. Independent readable-machine tickets continue. Manual operation is a connectivity fallback, not a second dispatch loop.
