cd "$(git rev-parse --show-toplevel)"
target="${JENDHUB_DEPLOY_TARGET:-root@195.63.145.165}"
directory="${JENDHUB_DEPLOY_DIRECTORY:-/opt/jendhub}"
site_url="${JENDHUB_SITE_URL:-https://jend.leeeet.dev}"
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi
for name in OPENROUTER_API_KEY CLOUDFLARE_TUNNEL_TOKEN; do
  if [ -z "${!name:-}" ]; then
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

copy_files() {
  remote "mkdir -p $directory"
  tar --create --directory=deploy compose.yaml prometheus \
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

send_image() {
  docker save jendhub:latest | gzip --fast | remote docker load --quiet
}

live_generations() {
  compose exec -T postgres psql --username=jendhub --dbname=jendhub --tuples-only \
    --no-align --command="SELECT count(*) FROM generations WHERE state = 'live'" \
    2>/dev/null || echo 0
}

up() {
  docker build --file deploy/Dockerfile --tag jendhub:latest \
    --build-arg JENDHUB_VERSION="$(git describe --always --dirty)" .
  copy_files
  send_image
  compose up --detach --wait postgres
  if [ "$(live_generations)" = 0 ] && [ "${1:-}" != --fresh ]; then
    echo "jendhub-deploy: the server has no live index." >&2
    echo "Run 'nix run .#deploy -- up --fresh' to build one from BendHub," >&2
    echo "which pays OpenRouter for each embedding." >&2
    exit 1
  fi
  compose up --detach --wait --remove-orphans
  remote docker image prune --force > /dev/null
  echo "deployed to $site_url"
}

case "${1:-up}" in
  up) shift || true; up "$@" ;;
  help | --help)
    echo "usage: nix run .#deploy -- [up [--fresh] | <docker compose command>]"
    ;;
  *) compose "$@" ;;
esac
