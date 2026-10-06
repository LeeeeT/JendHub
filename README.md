# JendHub

A search engine for BendHub definitions. See [docs/jev.md](docs/jev.md) for
the design and the Jev measurements.

## Setup

Put `OPENROUTER_API_KEY=...` in `.env`. The dev shell removes carriage returns
when it reads `.env`, so Windows line endings are permitted. A key with a space
or a control character stops the program with an error that does not show the
key. Then open the dev shell:

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
`data/hub/index/cache.sqlite`. A repeated query costs nothing. A change to the
code of the ranking starts a new cache, so after such a change each query
costs money again one time.

## Web server

```sh
python -m jend.web --port 8000
```

The server loads the index one time and serves two routes:

- `GET /?q=…`: an HTML page. It works without JavaScript. The colors follow
  the system setting: black on white, or white on black.
- `GET /search.json?q=…`: the best 20 results as compact JSON for programs
  and LLMs: `{"query": …, "results": [{"score", "signature", "summary",
  "import", "source"}]}`. `import` is the line to write (`import Base` for
  Base), and `source` is the file URL with the line as `#L…`.

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

`data/benchmark.json` holds 100 queries in the style of LLM coding agents, with
graded relevance judgments. Each judgment gives a document key, a grade, the
definition and the reason for the grade:

- 3: the definition does what the query asks.
- 2: it does the job with small extra work, or it does the central part.
- 1: it is related: a helper of an answer, the type of an answer, a proof about
  an answer, the inverse operation.
- 0: it is not relevant.

A query without a grade 2 or 3 judgment has no answer in the corpus. The
judgments cover the union of the best 20 results of BM25, of vector search and
of Jev, and answers found by a search of the corpus.

Measure the search:

```sh
python -m jend.evaluate --label baseline
```

The command runs the full pipeline for each query, without the result cache,
and costs approximately $0.05. `--retrieval` skips Jev and measures only the
retrieval stages, for the cost of the query embeddings. The command writes the
run to `data/hub/runs/` and prints:

- nDCG@10, the share of queries with a grade 3 result first, MRR and recall@20,
  on the queries with an answer.
- For each stage (BM25, vector search, the fused candidates, the final
  ranking), the share of answers in the best 10, 25, 50 and 100.
- How well the best score separates queries with an answer from queries
  without one (AUC).
- nDCG@10 for each query style, the queries with the lowest nDCG@10, and the
  results in the top 10 that have no judgment. When the share of results
  without a judgment increases, add judgments for them.

Compare two runs, or score a saved run again after the judgments change:

```sh
python -m jend.evaluate --compare data/hub/runs/A.json data/hub/runs/B.json
python -m jend.evaluate --report data/hub/runs/A.json
```

`--against RUN` compares a new run with `RUN` immediately. The comparison
gives the mean difference for each measure, a 95% bootstrap interval over the
queries, and the queries that changed most. Jev gives slightly different
probabilities for the same request, so two runs of the same algorithm differ
too, by approximately 0.01 nDCG@10. A difference is real only when it is
larger and its interval does not contain 0.

[docs/benchmark.md](docs/benchmark.md) tells how the queries and judgments
were made, gives the rules to grade new results, and gives the results of the
current design.
