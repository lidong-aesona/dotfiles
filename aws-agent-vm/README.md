# Agent VM

Live stack: `agent-vm-w1` in `us-west-1`. Tailscale hostname: `agent-vm-w1`.
Login: `ssh agent-vm-w1` (`ec2-user`, key `~/.ssh/workbox`). `Host workbox` still points at the old offline node.

## What a merge does

A push to `main` (an owner PR merge, or a direct push) runs `.github/workflows/agent-vm.yml`:

1. Assumes `dotfiles-agent-vm-deploy` via GitHub OIDC. No long-lived AWS keys.
2. Creates a CloudFormation change set from `cloudformation.yaml`.
3. Executes it only if nothing would be replaced. A UserData edit or a new AMI replaces the instance and orphans the root disk (`DeleteOnTermination: false` does not reattach it). That path exits non-zero unless someone runs `ALLOW_INSTANCE_REPLACEMENT=1`.
4. SSM-runs `apply-on-instance.sh` on the current instance: `~/.dotfiles` is reset to `origin/main` and `./rebuild.sh` applies home-manager. Credentials, sessions, and Claude `settings.json` policy stay on the box.

`LatestAmiId` is pinned to the AMI the instance booted (`ami-0a0512cc1f10345ce`). The previous SSM-latest parameter re-resolved on every deploy and would have replaced the instance as soon as Amazon published a new image.

## Update from the laptop

```bash
export AWS_PROFILE=aesona AWS_REGION=us-west-1
./aws-agent-vm/deploy.sh          # infrastructure only
./aws-agent-vm/apply-config.sh    # dotfiles only
./aws-agent-vm/deploy.sh --apply  # both
```

The first deploy after the AMI parameter became a plain AMI id must pass the running image, or CloudFormation will try to store the old SSM path as the image id:

```bash
AMI_ID=ami-0a0512cc1f10345ce ./aws-agent-vm/deploy.sh
```

After that, omit `AMI_ID`. The stack keeps the previous value.

Tailscale auth key and the SSH public key stay in the stack (`UsePreviousValue`). Do not commit them.

## Not in user-data

Dotfiles bootstrap does not belong in `UserData`. Changing that property replaces the instance. Base packages (docker, node, python, gh, mosh, herdr, claude, pi, tailscale) stay in user-data because a brand-new disk needs them before Nix exists.
