# JendHub

A search engine for BendHub definitions. See [docs/jev.md](docs/jev.md) for
the design and the Jev measurements.

## Setup

Put `OPENROUTER_API_KEY=...` in `.env`. Then open the dev shell:

```sh
nix develop
```

## Mirror

Mirror BendHub and extract the definition signatures:

```sh
python -m jend.mirror
```

The command downloads the `.bend` files of all packages to `data/hub/files/`.
A package hash identifies its content, so the command downloads only files
that are not in that directory. It also downloads Base (`bend2/base.bend`) from
the latest commit on the `main` branch of `bendlang/bend`. It writes the
signatures to `data/hub/mirror.json`.

For each declaration, the mirror keeps:

- `def`: the header up to the `:` that starts the body. A `def` without a
  return type fills a law with a proof, so the mirror skips it.
- `type`: the header and the constructors.
- `law`: the full statement.
- The `#` comment lines immediately above the declaration, as `doc`.

## Index

Enrich and embed the definitions of Base and of the 50 hottest packages (latest
version of each package). This step costs money, so `--budget` sets a limit in
USD for the enrichment:

```sh
python -m jend.index --budget 1.0
```

The command keeps its results in `data/hub/index/`. It sends only definitions
that do not have a result yet, so a second run costs only the new work.

## Search

```sh
python -m jend.search "decompress gzip data"
```

The command shows the 10 best of 50 candidates (`--top` changes this), in
descending order of Jev's probability. The result cache is in
`data/hub/index/cache.sqlite`. A repeated query costs nothing.

## Web server

```sh
python -m jend.web --port 8000
```

The server loads the index one time and serves two routes:

- `GET /?q=…`: an HTML page. It works without JavaScript. The colors follow
  the system setting: black on white, or white on black.
- `GET /search.json?q=…`: the same 50 results as JSON. Each result has
  `probability`, `kind`, `name`, `signature`, `summary`, `package` (`name`,
  `version`, `hash`), `file`, `line`, `import` (`null` for Base) and `source`.

A query that is in the cache costs nothing and has no limit. A new query costs
approximately $0.00042. The server refuses a new query with HTTP 429 and a
`Retry-After` header when:

- the client sent `JEND_RATE_PER_MINUTE` new queries in the last minute
  (default 10), or
- the server spent `JEND_DAILY_BUDGET_USD` today, in UTC (default 1.0). The
  spending is in `data/hub/index/budget.sqlite`, so a restart does not reset
  it. Queries that run at the same time can go a little over the budget.

A query longer than 200 characters gets HTTP 400. The rate limit counts each
IPv4 address, and each IPv6 /64 network. By default the address is the peer of
the connection. Behind a proxy that always sets a header with the client
address, set `JEND_CLIENT_IP_HEADER` to the name of that header (for example
`cf-connecting-ip` behind Cloudflare). Only do this when no client can reach
the server directly, because a client can write any value in that header.

Every response has a strict Content-Security-Policy (the page has no
scripts), `X-Content-Type-Options`, `Referrer-Policy` and
`Strict-Transport-Security`. `/robots.txt` keeps crawlers away from result
pages, because each new query costs money.

## Docker

The image holds the code only. Mount the data directory (`mirror.json` and
`index/`) at `/data`. The server writes its cache and its spending to that
directory, so the container user must be able to write there:

```sh
docker build -t jendhub .
docker run --user "$(id -u):$(id -g)" --env-file .env \
  -v "$PWD/data/hub:/data" -p 8000:8000 jendhub
```

The same image runs the other commands, for example
`docker run … jendhub jend-mirror --data /data`. `uv.lock` pins the Python
dependencies of the image; `uv lock` updates it after a change in
`pyproject.toml`.

## Production

`deploy/compose.yaml` runs the image and a Cloudflare Tunnel. No port is open
on the host: `cloudflared` connects out to Cloudflare, and Cloudflare sends
the requests for the public hostname to `http://app:8000`. The directory on
the server holds:

- `compose.yaml`
- `app.env`: `OPENROUTER_API_KEY=…` (mode 600)
- `tunnel.env`: `TUNNEL_TOKEN=…` (mode 600)
- `data/`: `mirror.json` and `index/`, owned by user 10001 (the user in the
  image)

To update the code, build the image, load it on the server and restart:

```sh
docker build -t jendhub:latest .
docker save jendhub:latest | ssh root@host docker load
ssh root@host 'cd /opt/jendhub && docker compose up -d'
```

## Evaluation

`data/queries.json` holds labeled queries on the real packages. Measure the
search on them:

```sh
python -m jend.evaluate
```
