{
  description = "JendHub: a search engine for BendHub definitions";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      pythonFor =
        pkgs:
        pkgs.python3.override {
          self = pythonFor pkgs;
          packageOverrides = pyself: pysuper: {
            typesafe-sdk = pyself.buildPythonPackage rec {
              pname = "typesafe_sdk";
              version = "0.7.2";
              format = "wheel";
              src = pkgs.fetchPypi {
                inherit pname version format;
                dist = "py3";
                python = "py3";
                hash = "sha256-CpYRSBh9UuGCdu1/LQJhfPrEjjuXZzy2Mah0k5fUPR4=";
              };
              dependencies = with pyself; [
                httpx2
                pydantic
                pydantic-core
                tenacity
                typing-extensions
              ];
              pythonImportsCheck = [ "typesafe_sdk" ];
            };

            jend = pyself.buildPythonPackage {
              pname = "jend";
              version = "0.1.0";
              pyproject = true;
              src = self;
              build-system = [ pyself.hatchling ];
              dependencies = with pyself; [
                fastapi
                httpx2
                numpy
                pydantic
                typesafe-sdk
                uvicorn
              ];
              pythonImportsCheck = [ "jend" ];
            };
          };
        };
    in
    {
      packages = forAllSystems (pkgs: {
        default = (pythonFor pkgs).pkgs.jend;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            ((pythonFor pkgs).withPackages (ps: [
              ps.fastapi
              ps.httpx
              ps.httpx2
              ps.numpy
              ps.pydantic
              ps.typesafe-sdk
              ps.uvicorn
              ps.pytest
            ]))
            pkgs.ruff
            pkgs.pyright
            pkgs.uv
          ];
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            if [ -f .env ]; then
              set -a
              . <(tr -d '\r' < .env)
              set +a
            fi
          '';
        };
      });
    };
}
