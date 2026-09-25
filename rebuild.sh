#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ln -sfn "$DIR" "$HOME/.dotfiles"

if ! command -v nix >/dev/null 2>&1 && [[ -f /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]]; then
  # shellcheck disable=SC1091
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi

case "$(uname -s)" in
  Darwin)
    exec sudo darwin-rebuild switch --flake "$HOME/.dotfiles#mac"
    ;;
  Linux)
    case "$(uname -m)" in
      x86_64) attr=agent-vm ;;
      aarch64) attr=agent-vm-aarch64 ;;
      *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
    esac
    # -b backup: the VM may already have hand-copied copies of linked files.
    if command -v home-manager >/dev/null 2>&1; then
      exec home-manager switch -b backup --flake "$HOME/.dotfiles#$attr"
    fi
    exec nix run home-manager/release-26.05 -- switch -b backup --flake "$HOME/.dotfiles#$attr"
    ;;
  *)
    echo "unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac
