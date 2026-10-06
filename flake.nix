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
