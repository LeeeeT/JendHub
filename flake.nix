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
      image = service: pkgs.dockerTools.buildLayeredImage {
        name = "jendhub-${service}";
        tag = "latest";
        contents = [ python jendhub pkgs.cacert pkgs.dockerTools.fakeNss ];
        extraCommands = "mkdir -p data/library tmp && chmod 1777 tmp";
        fakeRootCommands = "chown -R 65534:65534 data";
        config = {
          Entrypoint = [ "${jendhubService}/bin/jendhub-service" ];
          Cmd = [ service "--host" "0.0.0.0" "--port" "8000" ];
          Env = [
            "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            "JENDHUB_LIBRARY_PATH=/data/library"
          ];
          ExposedPorts."8000/tcp" = { };
          User = "65534:65534";
          WorkingDir = "/data";
        };
      };
      serviceNames = [ "api" "search" "syncer" "worker" ];
      apiSchema = pkgs.runCommand "jendhub-api-openapi.json" { } ''
        PYTHONPATH=${source} ${python}/bin/python -m jendhub.services api --openapi > $out
      '';
      nodejs = pkgs.nodejs_24;
      web = pkgs.buildNpmPackage {
        pname = "jendhub-web";
        version = "0.1.0";
        inherit nodejs;
        src = pkgs.lib.fileset.toSource {
          root = ./web;
          fileset = pkgs.lib.fileset.difference ./web (pkgs.lib.fileset.unions [
            (pkgs.lib.fileset.maybeMissing ./web/node_modules)
            (pkgs.lib.fileset.maybeMissing ./web/build)
            (pkgs.lib.fileset.maybeMissing ./web/.svelte-kit)
            (pkgs.lib.fileset.maybeMissing ./web/src/lib/api/schema.d.ts)
          ]);
        };
        npmDeps = pkgs.importNpmLock { npmRoot = ./web; };
        npmConfigHook = pkgs.importNpmLock.npmConfigHook;
        preBuild = ''
          npx openapi-typescript ${apiSchema} --output src/lib/api/schema.d.ts
          npx svelte-kit sync
        '';
        doCheck = true;
        checkPhase = ''
          runHook preCheck
          npm run check
          npm run lint
          npx vitest run
          runHook postCheck
        '';
        installPhase = ''
          runHook preInstall
          npm prune --omit=dev --offline
          mkdir -p $out/lib/jendhub-web
          cp -r build node_modules package.json $out/lib/jendhub-web/
          runHook postInstall
        '';
      };
      webImage = pkgs.dockerTools.buildLayeredImage {
        name = "jendhub-web";
        tag = "latest";
        contents = [ nodejs pkgs.dockerTools.fakeNss ];
        config = {
          Entrypoint = [ "${nodejs}/bin/node" "${web}/lib/jendhub-web/build/index.js" ];
          Env = [ "HOST=0.0.0.0" "PORT=8000" "NODE_ENV=production" ];
          ExposedPorts."8000/tcp" = { };
          User = "65534:65534";
        };
      };
      stackImages = map image serviceNames ++ [ webImage ];
      stack = pkgs.writeShellApplication {
        name = "jendhub-stack";
        runtimeInputs = [ pkgs.postgresql_17 ];
        text = ''
          export POSTGRES_PASSWORD="''${POSTGRES_PASSWORD:-jendhub-local}"
          export JENDHUB_API_PORT="''${JENDHUB_API_PORT:-18080}"
          export JENDHUB_WEB_PORT="''${JENDHUB_WEB_PORT:-18090}"
          if [ -z "''${OPENROUTER_API_KEY:-}" ] && [ -f .env ]; then
            set -a
            # shellcheck disable=SC1091
            source ./.env
            set +a
          fi
          if [ -z "''${OPENROUTER_API_KEY:-}" ]; then
            echo "jendhub-stack: set OPENROUTER_API_KEY, or put it in .env" >&2
            exit 1
          fi

          compose() {
            docker compose --project-name jendhub-local --file ${./deploy/compose.yaml} "$@"
          }

          load_images() {
            for image in ${pkgs.lib.concatStringsSep " " (map toString stackImages)}; do
              docker load --quiet < "$image"
            done
          }

          live_generations() {
            compose exec -T postgres psql --username=jendhub --dbname=jendhub --tuples-only \
              --no-align --command="SELECT count(*) FROM generations WHERE state = 'live'" \
              2>/dev/null || echo 0
          }

          seed() {
            local socket="$PWD/data/postgres/socket"
            if ! pg_isready --quiet --host="$socket" --username=jendhub; then
              echo "jendhub-stack: start the dev stores first: nix develop -c process-compose up" >&2
              exit 1
            fi
            load_images
            compose rm --stop --force syncer worker search api web qdrant
            docker volume rm --force jendhub-local_qdrant > /dev/null
            compose up --detach --wait postgres qdrant
            echo "copying the dev database"
            pg_dump --host="$socket" --username=jendhub --dbname=jendhub --format=custom \
              --no-owner \
              | compose exec -T postgres pg_restore --username=jendhub --dbname=jendhub \
                --clean --if-exists --no-owner --single-transaction
            echo "writing the vectors from the embedding cache"
            compose run --rm --no-deps --entrypoint jendhub migrate index restore-vectors
          }

          up() {
            load_images
            compose up --detach --wait postgres
            if [ "$(live_generations)" = 0 ] && [ "''${1:-}" != --fresh ]; then
              echo "jendhub-stack: the stack has no live index." >&2
              echo "Run 'nix run .#stack -- seed' to copy the dev index, or 'nix run .#stack -- up --fresh'" >&2
              echo "to build one from BendHub, which pays OpenRouter for each embedding." >&2
              exit 1
            fi
            compose up --detach --wait
            echo "web: http://127.0.0.1:$JENDHUB_WEB_PORT  api: http://127.0.0.1:$JENDHUB_API_PORT"
          }

          case "''${1:-}" in
            seed) seed ;;
            up) shift; up "$@" ;;
            "" | help | --help)
              echo "usage: nix run .#stack -- seed | up [--fresh] | <docker compose command>"
              ;;
            *) compose "$@" ;;
          esac
        '';
      };
    in
    {
      apps.x86_64-linux.stack = {
        type = "app";
        program = "${stack}/bin/jendhub-stack";
      };

      packages.x86_64-linux = {
        default = jendhub;
        inherit web;
        image-web = webImage;
      } // builtins.listToAttrs (map (service: {
        name = "image-${service}";
        value = image service;
      }) serviceNames);

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          devPython
          jendhub
          jendhubService
          indexer
          pkgs.pyright
          pkgs.ruff
          pkgs.process-compose
          nodejs
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

      checks.x86_64-linux.web = web;

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
