cd "$(git rev-parse --show-toplevel)"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-jendhub-local}"
export JENDHUB_SITE_PORT="${JENDHUB_SITE_PORT:-18080}"
export JENDHUB_PROMETHEUS_PORT="${JENDHUB_PROMETHEUS_PORT:-19090}"
export JENDHUB_SITE_URL="${JENDHUB_SITE_URL:-http://127.0.0.1:$JENDHUB_SITE_PORT}"
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
  echo "jendhub-stack: set OPENROUTER_API_KEY, or put it in .env" >&2
  exit 1
fi

compose() {
  docker compose --project-name jendhub-local --file deploy/compose.yaml \
    --file deploy/compose.local.yaml "$@"
}

live_generations() {
  compose exec -T postgres psql --username=jendhub --dbname=jendhub --tuples-only \
    --no-align --command="SELECT count(*) FROM generations WHERE state = 'live'" \
    2>/dev/null || echo 0
}

up() {
  docker build --file deploy/Dockerfile --tag jendhub:latest .
  compose up --detach --wait postgres
  if [ "$(live_generations)" = 0 ] && [ "${1:-}" != --fresh ]; then
    echo "jendhub-stack: the stack has no live index." >&2
    echo "Run 'nix run .#stack -- up --fresh' to build one from BendHub," >&2
    echo "which pays OpenRouter for each embedding." >&2
    exit 1
  fi
  compose up --detach --wait --remove-orphans
  echo "site: $JENDHUB_SITE_URL  prometheus: http://127.0.0.1:$JENDHUB_PROMETHEUS_PORT"
}

case "${1:-}" in
  up) shift; up "$@" ;;
  "" | help | --help)
    echo "usage: nix run .#stack -- up [--fresh] | <docker compose command>"
    ;;
  *) compose "$@" ;;
esac
