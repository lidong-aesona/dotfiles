#!/usr/bin/env bash
# First-time setup for the Amazon Linux agent VM.
# After this, use ./rebuild.sh on either machine.
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
  echo "bootstrap-linux.sh is for the agent VM. On a Mac, use ./bootstrap.sh." >&2
  exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

echo "==> Determinate Nix"
if command -v nix >/dev/null 2>&1; then
  echo "    nix already installed, skipping"
else
  curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix \
    | sh -s -- install --no-confirm
  # shellcheck disable=SC1091
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi

echo "==> symlink this repo to ~/.dotfiles"
# A real directory at ~/.dotfiles is a checkout: `ln` would plant the link inside it.
if [[ -d "$HOME/.dotfiles" && ! -L "$HOME/.dotfiles" ]]; then
  [[ "$(cd "$HOME/.dotfiles" && pwd -P)" == "$DIR" ]] || { echo "$HOME/.dotfiles is another checkout, not $DIR" >&2; exit 1; }
else
  ln -sfn "$DIR" "$HOME/.dotfiles"
fi

echo "==> home-manager switch"
"$DIR/rebuild.sh"

echo "==> Done. Open a new shell so zsh, starship, and the nix profile are on PATH."
