{
  description = "voice-activate-claude development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        # .python-version を唯一の真実として nix 側の Python も選ぶ。
        # Renovate が .python-version を上げれば devShell も自動追従するため、
        # flake.nix の python 属性名を更新し忘れて CI が壊れる事故を防ぐ。
        pyVersion = builtins.replaceStrings [ "\n" "\r" ] [ "" "" ]
          (builtins.readFile ./.python-version);
        pyParts = pkgs.lib.splitString "." pyVersion;
        python = pkgs."python${builtins.elemAt pyParts 0}${builtins.elemAt pyParts 1}";
      in {
        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
            pkgs.uv
          ];
          # uv の managed Python は NixOS では動かないため、nix 提供の Python を使わせる
          UV_PYTHON_DOWNLOADS = "never";
          # nix の Python はシステムの ld パスを見ないため、numpy 等の
          # manylinux wheel が要求する libstdc++ を明示的に通す
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
        };
      });
}
