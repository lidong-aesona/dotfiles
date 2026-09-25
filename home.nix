{ config, lib, pkgs, user, ... }:

let
  dotfiles = "${config.home.homeDirectory}/.dotfiles";
  darwin = pkgs.stdenv.isDarwin;
  link = path: config.lib.file.mkOutOfStoreSymlink "${dotfiles}/${path}";
  # force: the VM already has hand-copied files. Replace them with the repo symlink.
  linked = path: {
    source = link path;
    force = true;
  };
in

{
  home.username = user;
  home.homeDirectory = if darwin then "/Users/${user}" else "/home/${user}";
  home.stateVersion = "24.11";
  home.packages = with pkgs; [
    # cli i use constantly
    ripgrep   # fast search
    fd        # fast find
    fzf       # fuzzy finder
    jq        # json on the command line
    lazygit
    neovim
    awscli2   # aws
    gh        # github
    shellcheck
    actionlint
  ] ++ lib.optionals darwin [
    # the font everything renders in. The VM is headless; WezTerm on the Mac draws the glyphs.
    nerd-fonts.hack
  ];
  fonts.fontconfig.enable = darwin;
  home.sessionVariables.EDITOR = "nvim";
  # Non-interactive ssh on the VM does not source bashrc. These stay off the Mac PATH.
  home.sessionPath = lib.optionals (!darwin) [
    "$HOME/.nix-profile/bin"
    "$HOME/.local/bin"
    "$HOME/google-cloud-sdk/bin"
  ];

  programs.zsh = {
    enable = true;
    autosuggestion.enable = true;      # ghost text from history
    syntaxHighlighting.enable = true;  # commands turn green when valid
    history.findNoDups = true;         # arrow-up search skips repeated matches
    initContent = ''
      bindkey '^f' autosuggest-accept

      # Arrow keys search history by what is already typed, like oh-my-zsh did.
      autoload -U up-line-or-beginning-search down-line-or-beginning-search
      zle -N up-line-or-beginning-search
      zle -N down-line-or-beginning-search
      bindkey '^[[A' up-line-or-beginning-search
      bindkey '^[[B' down-line-or-beginning-search
      bindkey '^[OA' up-line-or-beginning-search
      bindkey '^[OB' down-line-or-beginning-search
    '' + lib.optionalString (!darwin) ''
      # Installed by hand on the VM; bashrc sources the same SDK.
      if [ -f "$HOME/google-cloud-sdk/path.zsh.inc" ]; then
        . "$HOME/google-cloud-sdk/path.zsh.inc"
      fi
    '';
    shellAliases = {
      ".." = "cd ..";
      add = "git add .";
      push = "git push";
      pull = "git pull";
      m = "git switch main";
      cc = "claude --dangerously-skip-permissions";
      co = "codex --full-auto";
    };
  };

  programs.starship = {
    enable = true;
    settings = {
      add_newline = false;
      format = "$directory$git_branch$git_status$cmd_duration$line_break$character";
      character = {
        success_symbol = "[❯](purple)";
        error_symbol = "[❯](red)";
      };
      cmd_duration.format = "[$duration]($style) ";
    };
  };

  # Edit-in-place: the real file stays in my repo, ~/.config just points at it.
  # Mac links the whole herdr directory (runtime files are gitignored).
  # Linux links only config.toml so session, sockets, and logs stay on that machine.
  # Claude settings.json names trusted repos, so Linux keeps a real per-machine file.
  home.file = lib.mkMerge [
    {
      ".config/wezterm" = linked "home/.config/wezterm";
      ".config/nvim" = linked "home/.config/nvim";
      ".claude/statusline.sh" = linked "home/.claude/statusline.sh";
      ".pi/agent/themes" = linked "home/.pi/agent/themes";
      ".pi/agent/extensions" = linked "home/.pi/agent/extensions";
      ".pi/agent/models.json" = linked "home/.pi/agent/models.json";
      ".pi/agent/settings.json" = linked "home/.pi/agent/settings.json";
      ".agents/AGENTS.md" = linked "home/AGENTS.md";
      ".claude/CLAUDE.md" = linked "home/AGENTS.md";
      ".codex/AGENTS.md" = linked "home/AGENTS.md";
      ".config/opencode/AGENTS.md" = linked "home/AGENTS.md";
      ".pi/agent/AGENTS.md" = linked "home/AGENTS.md";
    }
    (lib.mkIf darwin {
      ".config/herdr" = linked "home/.config/herdr";
      ".claude/settings.json" = linked "home/.claude/settings.json";
    })
    (lib.mkIf (!darwin) {
      ".config/herdr/config.toml" = linked "home/.config/herdr/config.toml";
    })
  ];

  # Claude settings.json is not linked on Linux: it names trusted repo paths.
  # Copy only the keys that are safe to share, and leave autoMode/permissions alone.
  home.activation.claudeSharedSettings = lib.mkIf (!darwin) (
    lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      f="$HOME/.claude/settings.json"
      shared="${dotfiles}/home/.claude/settings.json"
      install -d "$HOME/.claude"
      if [[ ! -e "$f" ]]; then
        printf '%s\n' '{}' > "$f"
      fi
      if [[ -L "$f" ]]; then
        echo "refusing to merge into symlinked $f" >&2
        exit 1
      fi
      tmp="$(mktemp)"
      ${pkgs.jq}/bin/jq --slurpfile shared "$shared" '
        . as $local
        | $shared[0]
        | {
            statusLine,
            enabledPlugins,
            voice,
            voiceEnabled,
            skipDangerousModePermissionPrompt,
            theme,
            agentPushNotifEnabled,
            modelSettings
          }
        | with_entries(select(.value != null))
        | $local + .
      ' "$f" > "$tmp"
      mv "$tmp" "$f"
    ''
  );

  # Standalone home-manager cannot set the login shell. ~/.nix-profile/bin/zsh is stable across store updates.
  home.activation.ensureZshLoginShell = lib.mkIf (!darwin) (
    # installPackages creates ~/.nix-profile/bin/zsh. This must run after that.
    lib.hm.dag.entryAfter [ "installPackages" ] ''
      shell="$HOME/.nix-profile/bin/zsh"
      if [[ ! -x "$shell" ]]; then
        echo "zsh missing at $shell" >&2
        exit 1
      fi
      if ! grep -qxF "$shell" /etc/shells; then
        echo "$shell" | /usr/bin/sudo tee -a /etc/shells >/dev/null
      fi
      # AL2023 has usermod, not chsh (chsh is in util-linux-user, which is not installed).
      current="$(/usr/bin/getent passwd "$USER" | cut -d: -f7)"
      if [[ "$current" != "$shell" ]]; then
        /usr/bin/sudo /usr/sbin/usermod -s "$shell" "$USER"
      fi
    ''
  );
}
