{
  description = "JendHub";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
      dependencies = ps: [ ps.httpx ps.numpy ];
      python = pkgs.python314.withPackages dependencies;
      devPython = pkgs.python314.withPackages (ps:
        dependencies ps ++ [ ps.pytest ps.respx ]);
      jendhub = pkgs.writeShellApplication {
        name = "jendhub";
        runtimeInputs = [ python ];
        text = ''
          export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}${./src}"
          exec python -m jendhub "$@"
        '';
      };
    in
    {
      packages.x86_64-linux.default = jendhub;

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [ devPython jendhub pkgs.pyright pkgs.ruff ];
        shellHook = ''
          export PYTHONPATH="$PWD/src"
          if [ -f .env ]; then
            set -a
            source ./.env
            set +a
          fi
        '';
      };

      checks.x86_64-linux.default = pkgs.runCommand "jendhub-checks" {
        nativeBuildInputs = [ devPython pkgs.pyright pkgs.ruff ];
        SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
      } ''
        cp -r ${pkgs.lib.fileset.toSource {
          root = ./.;
          fileset = pkgs.lib.fileset.unions [ ./src ./tests ./pyproject.toml ];
        }} source
        chmod -R u+w source
        cd source
        export PYTHONPATH="$PWD/src"
        pytest
        ruff check
        ruff format --check
        pyright --pythonpath ${devPython}/bin/python
        touch "$out"
      '';
    };
}
