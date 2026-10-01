# Project notes for agents

Deliberate decisions in this repo - do NOT silently revert them:

- `homebrew.onActivation.cleanup = "zap"` in `configuration.nix` is intentional. It forces the good habit of declaring every Homebrew package in the Nix config instead of installing things ad-hoc, which keeps the machine reproducible. Do not soften it to `uninstall` or `none`. Users are warned about its effect in README.md; this note is for anyone tempted to change the setting itself.
- Never commit `.no-mistakes/` validation evidence to this public repo. `.no-mistakes/` is gitignored; if a validation pipeline stages evidence into a branch, drop it before merging.
- The Linux agent VM is `homeConfigurations.agent-vm` (Graviton: `agent-vm-aarch64`). Do not commit Pi auth, Claude credentials, or sessions here. Registry-installed skills stay out of the repo and `sync-skills.sh` copies them between machines. Skills written here live in `home/.agents/skills/` and are linked by `home.nix`; this repo is public, so keep private repository names and account ids out of them. The VM deploy hard-resets its checkout to main, so commit skill edits made there or they are lost. Claude `settings.json` stays per machine because it names trusted repos.
- Live EC2 stack is `agent-vm-w1` in `us-west-1`, template `aws-agent-vm/cloudformation.yaml`. Do not put dotfiles bootstrap in UserData. `aws-agent-vm/deploy.sh` refuses a change set that replaces any resource. `LatestAmiId` is pinned; the SSM-latest AMI parameter replaces the instance whenever Amazon publishes a new image.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
