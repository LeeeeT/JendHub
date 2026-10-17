{
  description = "JendHub";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.self.submodules = true;

  outputs = { nixpkgs, ... }:
    let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
      dependencies = ps: [
        ps.fastapi
        ps.httpx
        ps.jinja2
        ps.lightgbm
        ps.numpy
        ps.prometheus-client
        ps.psycopg
        ps.psycopg-pool
        ps.pydantic
        ps.pydantic-settings
        ps.qdrant-client
        ps.uvicorn
      ];
      qdrant = pkgs.stdenvNoCC.mkDerivation rec {
        pname = "qdrant";
        version = "1.18.2";
        src = pkgs.fetchurl {
          url = "https://github.com/qdrant/qdrant/releases/download/v${version}/qdrant-x86_64-unknown-linux-musl.tar.gz";
          hash = "sha256-QKavRPikllYMnSNStrKgragWqkjQeBxo9gJYLmezrqA=";
        };
        sourceRoot = ".";
        installPhase = "install -Dm755 qdrant $out/bin/qdrant";
      };
      services = [ pkgs.postgresql_17 qdrant ];
      python = pkgs.python314.withPackages dependencies;
      devPython = pkgs.python314.withPackages (ps:
        dependencies ps ++ [ ps.pytest ps.pytest-asyncio ps.respx ]);
      indexerSource = pkgs.lib.fileset.toSource {
        root = ./.;
        fileset = pkgs.lib.fileset.unions [ ./indexer ./bend2-lsp-rs ];
      };
      indexer = pkgs.rustPlatform.buildRustPackage {
        pname = "jendhub-index";
        version = "0.1.0";
        src = indexerSource;
        cargoRoot = "indexer";
        buildAndTestSubdir = "indexer";
        cargoLock.lockFile = ./indexer/Cargo.lock;
      };
      indexerLints = indexer.overrideAttrs (old: {
        pname = "jendhub-index-lints";
        nativeBuildInputs = old.nativeBuildInputs ++ [ pkgs.clippy pkgs.rustfmt ];
        buildPhase = ''
          cd indexer
          cargo fmt --check
          cargo clippy --all-targets --locked --offline -- -D warnings
        '';
        doCheck = false;
        installPhase = "touch $out";
      });
      source = pkgs.lib.fileset.toSource {
        root = ./src;
        fileset = ./src/jendhub;
      };
      jendhub = pkgs.writeShellApplication {
        name = "jendhub";
        runtimeInputs = [ python indexer ];
        text = ''
          export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}${source}"
          exec python -m jendhub "$@"
        '';
      };
      jendhubService = pkgs.writeShellApplication {
        name = "jendhub-service";
        runtimeInputs = [ python indexer ];
        text = ''
          export PYTHONPATH="''${PYTHONPATH:+$PYTHONPATH:}${source}"
          exec python -m jendhub.services "$@"
        '';
      };
      stack = pkgs.writeShellApplication {
        name = "jendhub-stack";
        runtimeInputs = [ pkgs.gitMinimal ];
        text = builtins.readFile ./deploy/stack.sh;
      };
      deploy = pkgs.writeShellApplication {
        name = "jendhub-deploy";
        runtimeInputs = [ pkgs.gitMinimal pkgs.openssh pkgs.gnutar pkgs.gzip pkgs.coreutils ];
        text = builtins.readFile ./deploy/deploy.sh;
      };
    in
    {
      apps.x86_64-linux.stack = {
        type = "app";
        program = "${stack}/bin/jendhub-stack";
      };

      apps.x86_64-linux.deploy = {
        type = "app";
        program = "${deploy}/bin/jendhub-deploy";
      };

      packages.x86_64-linux.default = jendhub;

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          devPython
          jendhub
          jendhubService
          indexer
          pkgs.pyright
          pkgs.ruff
          pkgs.process-compose
        ] ++ services;
        shellHook = ''
          export PYTHONPATH="$PWD/src"
          export JENDHUB_DATABASE_URL="postgresql://jendhub@/jendhub?host=$PWD/data/postgres/socket"
          export JENDHUB_QDRANT_URL="http://localhost:6333"
          export PC_PORT_NUM=8070
          if [ -f .env ]; then
            set -a
            source ./.env
            set +a
          fi
        '';
      };

      checks.x86_64-linux.indexer = indexerLints;

      checks.x86_64-linux.default = pkgs.runCommand "jendhub-checks" {
        nativeBuildInputs = [ devPython indexer pkgs.pyright pkgs.ruff ] ++ services;
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
