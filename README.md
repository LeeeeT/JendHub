# JendHub

A search engine for BendHub definitions. See [docs/design.md](docs/design.md)
for the design and the measurements that selected it.

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

The command keeps its results in `data/hub/index/`:

- `enrichment.jsonl`: a summary and 3 likely queries for each definition.
- `text.npy` and `text.keys.json`: a vector of the full document text.
- `signature.npy` and `signature.keys.json`: a vector of the name and the
  signature.

It sends only definitions that do not have a result yet, so a second run costs
only the new work. A new definition costs approximately $0.00002 for the
enrichment and $0.000002 for the two vectors.

## Search

```sh
python -m jend.search "decompress gzip data"
```

The command shows the 10 best of 50 results (`--top` changes this), in
descending order of the ranker's score. The query vectors are in
`data/hub/index/embeddings.sqlite`, so a repeated query does not call
OpenRouter.

## Ranker

The ranker is a LambdaMART model (LightGBM) that is trained on the benchmark
judgments. `src/jend/ranker.json` holds the model, the names of its features,
and the calibration that changes a score into a probability. Train it again
after a change to the judgments, to the features or to the index:

```sh
python -m jend.ranker
```

## Web server

```sh
python -m jend.web --port 8000
```

The server loads the index one time and serves two routes:

- `GET /?q=…`: an HTML page. It works without JavaScript. The colors follow
  the system setting: black on white, or white on black.
- `GET /search.json?q=…`: the best 20 results as compact JSON for programs
  and LLMs: `{"query": …, "results": [{"score", "signature", "summary",
  "import", "source"}]}`. `score` is the estimated probability that the
  result does what the query asks. `import` is the line to write
  (`import Base` for Base), and `source` is the file URL with the line as
  `#L…`.

A new query costs one query embedding, approximately $0.0000003, so the server
has no rate limit and no budget. A query longer than 200 characters gets HTTP
400. When OpenRouter does not answer, the server returns HTTP 502.

Every response has a strict Content-Security-Policy (the page has no
scripts), `X-Content-Type-Options`, `Referrer-Policy` and
`Strict-Transport-Security`. `/robots.txt` keeps crawlers away from result
pages.

## Docker

The image holds the code and the ranker model. Mount the data directory
(`mirror.json` and `index/`) at `/data`. The server writes its query vectors to
that directory, so the container user must be able to write there:

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

The server uses approximately 700 MB of memory.

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

A query without a grade 2 or 3 judgment has no answer in the corpus.

Measure the search:

```sh
python -m jend.evaluate --label baseline
```

The ranker learns from the benchmark, so the shipped model cannot measure
itself. The command uses 5-fold cross-validation over the queries: it trains a
model on 4 parts and ranks the queries of the fifth part with that model. The
query vectors come from `data/hub/index/embeddings.sqlite`, so a second run
costs nothing and gives the same result. The command writes the run to
`data/hub/runs/` and prints:

- nDCG@10, the share of queries with a grade 3 result first, MRR and recall@20,
  on the queries with an answer.
- For each stage (BM25, vector search, the final ranking), the share of
  answers in the best 10, 25, 50 and 100.
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
queries, and the queries that changed most. A difference is real only when its
interval does not contain 0.

[docs/benchmark.md](docs/benchmark.md) tells how the queries and judgments
were made, gives the rules to grade new results, and gives the results of the
current design.
