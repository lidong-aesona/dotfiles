#!/usr/bin/env bash
# Runs on the VM as root, via SSM. Resets ~/.dotfiles to origin/main and applies it.
# Local edits in that checkout are discarded. Credentials and sessions are outside it.
set -euo pipefail

sudo -u ec2-user bash -lc '
set -euo pipefail
if [[ -f /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
  # shellcheck disable=SC1091
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi
export PATH="$HOME/.nix-profile/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
repo=https://github.com/lidong-aesona/dotfiles.git
if [[ ! -d "$HOME/.dotfiles/.git" ]]; then
  git clone "$repo" "$HOME/.dotfiles"
  "$HOME/.dotfiles/bootstrap-linux.sh"
else
  git -C "$HOME/.dotfiles" fetch "$repo" main
  git -C "$HOME/.dotfiles" reset --hard FETCH_HEAD
  git -C "$HOME/.dotfiles" clean -fd
  "$HOME/.dotfiles/rebuild.sh"
fi
'
