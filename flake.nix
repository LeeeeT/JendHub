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
        contents = [ python jendhub ];
        extraCommands = ''
          mkdir -p data/library tmp etc
          chmod 1777 tmp
          cp ${pkgs.dockerTools.fakeNss}/etc/passwd ${pkgs.dockerTools.fakeNss}/etc/group \
            ${pkgs.dockerTools.fakeNss}/etc/nsswitch.conf etc/
        '';
        fakeRootCommands = "chown -R 65534:65534 data";
        config = {
          Entrypoint = [ "${jendhubService}/bin/jendhub-service" ];
          Cmd = [ service "--host" "0.0.0.0" "--port" "8000" "--metrics-port" "9000" ];
          Env = [
            "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            "JENDHUB_LIBRARY_PATH=/data/library"
          ];
          ExposedPorts."8000/tcp" = { };
          User = "65534:65534";
          WorkingDir = "/data";
        };
      };
      serviceNames = [ "site" "syncer" "worker" ];
      images = map image serviceNames;
      imageList = pkgs.lib.concatStringsSep " " (map toString images);
      deployFiles = ./deploy;
      stack = pkgs.writeShellApplication {
        name = "jendhub-stack";
        runtimeInputs = [ pkgs.postgresql_17 ];
        text = ''
          export POSTGRES_PASSWORD="''${POSTGRES_PASSWORD:-jendhub-local}"
          export JENDHUB_SITE_PORT="''${JENDHUB_SITE_PORT:-18080}"
          export JENDHUB_PROMETHEUS_PORT="''${JENDHUB_PROMETHEUS_PORT:-19090}"
          export JENDHUB_SITE_URL="''${JENDHUB_SITE_URL:-http://127.0.0.1:$JENDHUB_SITE_PORT}"
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
            docker compose --project-name jendhub-local --file ${deployFiles}/compose.yaml \
              --file ${deployFiles}/compose.local.yaml "$@"
          }

          load_images() {
            for image in ${imageList}; do
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
            compose rm --stop --force syncer worker site qdrant
            docker volume rm --force jendhub-local_qdrant > /dev/null
            compose up --detach --wait postgres qdrant
            echo "copying the dev database"
            pg_dump --host="$socket" --username=jendhub --dbname=jendhub --format=custom \
              --no-owner \
              | compose exec -T postgres pg_restore --username=jendhub --dbname=jendhub \
                --clean --if-exists --no-owner --single-transaction
            compose run --rm migrate
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
            compose up --detach --wait --remove-orphans
            echo "site: $JENDHUB_SITE_URL  prometheus: http://127.0.0.1:$JENDHUB_PROMETHEUS_PORT"
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
      deploy = pkgs.writeShellApplication {
        name = "jendhub-deploy";
        runtimeInputs = [ pkgs.openssh pkgs.gnutar pkgs.coreutils ];
        text = ''
          target="''${JENDHUB_DEPLOY_TARGET:-root@195.63.145.165}"
          directory="''${JENDHUB_DEPLOY_DIRECTORY:-/opt/jendhub}"
          site_url="''${JENDHUB_SITE_URL:-https://jend.leeeet.dev}"
          if [ -f .env ]; then
            set -a
            # shellcheck disable=SC1091
            source ./.env
            set +a
          fi
          for name in OPENROUTER_API_KEY CLOUDFLARE_TUNNEL_TOKEN; do
            if [ -z "''${!name:-}" ]; then
              echo "jendhub-deploy: set $name, or put it in .env" >&2
              exit 1
            fi
          done

          remote() {
            # shellcheck disable=SC2029
            ssh "$target" "$@"
          }

          compose() {
            remote "cd $directory && docker compose --profile public $(printf '%q ' "$@")"
          }

          retire_old() {
            remote "if [ -f $directory/app.env ]; then
              mkdir -p /opt/jendhub-old
              mv $directory/app.env $directory/tunnel.env $directory/data \
                $directory/data.tar.gz $directory/image.tar.gz /opt/jendhub-old/ 2>/dev/null || true
              docker rm --force jendhub-app-1 > /dev/null 2>&1 || true
              echo 'moved the old deployment to /opt/jendhub-old'
            fi"
          }

          copy_files() {
            remote "mkdir -p $directory"
            tar --create --directory=${deployFiles} compose.yaml prometheus \
              | remote "tar --extract --no-same-owner --directory=$directory"
            local password
            password="$(remote "sed -n 's/^POSTGRES_PASSWORD=//p' $directory/.env 2>/dev/null" || true)"
            if [ -z "$password" ]; then
              password="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"
            fi
            printf '%s\n' \
              "POSTGRES_PASSWORD=$password" \
              "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" \
              "CLOUDFLARE_TUNNEL_TOKEN=$CLOUDFLARE_TUNNEL_TOKEN" \
              "JENDHUB_SITE_URL=$site_url" \
              "JENDHUB_CLIENT_IP_HEADER=cf-connecting-ip" \
              | remote "umask 077 && cat > $directory/.env"
          }

          load_images() {
            for image in ${imageList}; do
              remote docker load --quiet < "$image"
            done
          }

          live_generations() {
            compose exec -T postgres psql --username=jendhub --dbname=jendhub --tuples-only \
              --no-align --command="SELECT count(*) FROM generations WHERE state = 'live'" \
              2>/dev/null || echo 0
          }

          prepare() {
            retire_old
            copy_files
            load_images
          }

          seed() {
            prepare
            local source=(docker compose --project-name jendhub-local exec -T postgres)
            if ! "''${source[@]}" pg_isready --quiet --username=jendhub; then
              echo "jendhub-deploy: start the local stack first: nix run .#stack -- up" >&2
              exit 1
            fi
            compose rm --stop --force syncer worker site tunnel qdrant
            remote "docker volume rm --force jendhub_qdrant > /dev/null"
            compose up --detach --wait postgres qdrant
            echo "copying the database of the local stack"
            "''${source[@]}" pg_dump --username=jendhub --dbname=jendhub --format=custom \
              --no-owner --exclude-table-data=searches \
              | compose exec -T postgres pg_restore --username=jendhub --dbname=jendhub \
                --clean --if-exists --no-owner --single-transaction
            compose run --rm migrate
            compose exec -T postgres psql --username=jendhub --dbname=jendhub \
              --command="DELETE FROM generations WHERE corpus <> 'live'"
            echo "writing the vectors from the embedding cache"
            compose run --rm --no-deps --entrypoint jendhub migrate index restore-vectors
          }

          up() {
            prepare
            compose up --detach --wait postgres
            if [ "$(live_generations)" = 0 ] && [ "''${1:-}" != --fresh ]; then
              echo "jendhub-deploy: the server has no live index." >&2
              echo "Run 'nix run .#deploy -- seed' to copy the index of the local stack, or" >&2
              echo "'nix run .#deploy -- up --fresh' to build one, which pays for each embedding." >&2
              exit 1
            fi
            compose up --detach --wait --remove-orphans
            echo "deployed to $site_url"
          }

          case "''${1:-up}" in
            up) shift || true; up "$@" ;;
            seed) seed ;;
            help | --help)
              echo "usage: nix run .#deploy -- [up [--fresh] | seed | <docker compose command>]"
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

      apps.x86_64-linux.deploy = {
        type = "app";
        program = "${deploy}/bin/jendhub-deploy";
      };

      packages.x86_64-linux = {
        default = jendhub;
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
