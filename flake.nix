{
  description = "dotfiles";

  inputs = {
    # Use `github:NixOS/nixpkgs/nixpkgs-26.05-darwin` to use Nixpkgs 26.05.
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";
    # Use `github:nix-darwin/nix-darwin/nix-darwin-26.05` to use Nixpkgs 26.05.
    nix-darwin.url = "github:nix-darwin/nix-darwin/nix-darwin-26.05";
    nix-darwin.inputs.nixpkgs.follows = "nixpkgs";

    home-manager.url = "github:nix-community/home-manager/release-26.05";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";

    nix-homebrew.url = "github:zhaofengli/nix-homebrew";

    # Linux agent VM. Kept separate from the darwin nixpkgs pin above.
    nixpkgs-linux.url = "github:NixOS/nixpkgs/nixos-26.05";
  };

  outputs = inputs@{ self, nix-darwin, nix-homebrew, home-manager, nixpkgs, nixpkgs-linux }:
    let
      # The one username line to change if this isn't your machine.
      # bootstrap.sh offers to rewrite this for you if your macOS username differs.
      user = "lidongwei";
    in
    {
      darwinConfigurations."mac" = nix-darwin.lib.darwinSystem {
        specialArgs = { inherit user; };
        modules = [
          ./configuration.nix
          nix-homebrew.darwinModules.nix-homebrew
          home-manager.darwinModules.home-manager
          {
            home-manager.useGlobalPkgs = true;
            home-manager.useUserPackages = true;
            home-manager.extraSpecialArgs = { inherit user; };
            home-manager.users.${user} = import ./home.nix;
          }
        ];
      };

      # Amazon Linux agent VM. Username is the AMI default, not the Mac user above.
      # bootstrap-linux.sh / rebuild.sh pick the attr from `uname -m`.
      homeConfigurations = let
        linuxHome = system: home-manager.lib.homeManagerConfiguration {
          pkgs = import nixpkgs-linux {
            inherit system;
            config.allowUnfree = true;
          };
          extraSpecialArgs = { user = "ec2-user"; };
          modules = [ ./home.nix ];
        };
      in {
        agent-vm = linuxHome "x86_64-linux";
        agent-vm-aarch64 = linuxHome "aarch64-linux";
      };
    };
}
