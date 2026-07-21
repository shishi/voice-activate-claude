{
  description = "voice-activate-claude development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = nixpkgs.legacyPackages.${system};
      in {
        devShells.default = pkgs.mkShell {
          buildInputs = with pkgs; [
            python312
            uv
          ];
          # uv の managed Python は NixOS では動かないため、nix 提供の Python を使わせる
          UV_PYTHON_DOWNLOADS = "never";
          # nix の Python はシステムの ld パスを見ないため、numpy 等の
          # manylinux wheel が要求する libstdc++ を明示的に通す
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib ];
        };
      });
}
