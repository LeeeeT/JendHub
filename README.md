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

Enrich and embed the definitions of Base and of the 100 hottest packages
(latest version of each package). This step costs money, so `--budget` sets a
limit in USD for the enrichment:

```sh
python -m jend.build --budget 2.5
```

The command keeps the paid results in `data/hub/cache/`. Do not delete this
directory: a new enrichment of all definitions costs approximately $1.30.

- `enrichment.jsonl`: for each definition, its role (`api`, `helper`, `local`
  or `test`), a summary, for API definitions 3 likely queries and 2 or 3
  keywords, and the model and provider endpoint that wrote it. The
  enrichment uses only the `open-inference/fp4` endpoint of
  `deepseek/deepseek-v4-flash` (`enrich.PROVIDER`). When that endpoint does
  not answer, its batches fail, and the next run sends them again.
- `text.npy` and `text.keys.json`: a vector of the full document text.
- `signature.npy` and `signature.keys.json`: a vector of the name and the
  signature.

It sends only definitions that do not have a result yet, so a second run costs
only the new work. A new definition costs approximately $0.000023 for the
enrichment and $0.000002 for the two vectors. These costs were measured
before the enrichment used one provider; the price of `open-inference/fp4`
can make them different.

Then the command writes the search index to `data/hub/index/`. The server
reads only this directory:

- `index.sqlite`: the documents, one row for each document, and the BM25
  postings of each term.
- `text.codes.npy`, `signature.codes.npy` and their `.scale.npy` files: the
  two vectors of each document as int8, with one scale for each dimension.
  int8 does not change the ranking measurably.
- `sources.sqlite`: the text of every file of every package version in the
  mirror, and the label (`name@version`, or the hash) of each package
  version, approximately 70 MB. A relative import (`import ./x.bend as X`)
  becomes an import of the package path (`import name@version/dir/x.bend as
  X`), so copied code imports correctly from any project.

## Search

```sh
python -m jend.search "decompress gzip data"
```

The command shows the 10 best results (`--top` changes this), in descending
order of their score. A fixed formula (`jend.score`) computes the
score from vector similarity, BM25, the name, and signals that put the API
before helpers and proof files; [docs/design.md](docs/design.md#formula)
gives it. Nothing is trained. The query vectors are in
`data/hub/queries.sqlite`, so a repeated query does not call OpenRouter.

## Web server

```sh
python -m jend.web --port 8000
```

The text answers for LLMs give full URLs, because some fetch tools accept no
path without the host. `JEND_ORIGIN` sets the public origin of these URLs
(`deploy/compose.yaml` sets `https://jend.leeeet.dev`). Without it, the server
uses `http://<host>:<port>` from its arguments.

The server opens the index one time and serves these routes:

- `GET /?q=…`: an HTML page with the best 20 results. When the reader scrolls
  near the end of the list, a small script loads the next 20, until the end
  of the candidate pool (approximately 200 to 400 results). The colors follow
  the system setting: black on white, or white on black.
- `GET /more?q=…&start=N`: the HTML list items of the results from position
  `N`, 20 at a time. The script of the page uses this route.
- `GET /search.txt?q=…&start=N`: 10 results from position `N` (default 0) as
  plain text for LLMs, best first. Each result gives its rank, the name to use
  in code (`Alias.name`, or the plain name for Base), its score, its import
  line (`import Base` for Base), its declaration, the doc comment of its
  author, the summary, and the URL of its source under `/src/`. The score is
  the value of the ranking formula: a higher score is a better match, but the scores of
  two queries cannot be compared. When more results exist, the last line is
  `More results: ` and the URL of the next results.

  The first correct answer is in the best 10 for 94% of the dev queries and
  99% of the benchmark queries; ranks 11 to 20 add no query. A doc comment
  longer than 500 characters ends at a sentence with "…". An answer stops
  before 16,000 characters of results, and its last line then links to the
  rest. On the dev and benchmark queries, the median answer has 3,800
  characters and the largest has 5,900.
- `GET /src/<package>/`: the files of a package version. `<package>` is the
  label or the hash, so the target of any import line in a result or a file
  is a path under `/src/`.
  All package versions of the mirror are available, also the ones that the
  search does not cover, because files import exact versions.
- `GET /src/<package>/<file>`: the file as plain text. With `?def=<name>`, only
  that definition: the import lines that its body uses (not Base), its doc
  comment, its declaration and its body, up to the next top-level item. The
  search results and the HTML page link to this form.
- `GET /llms.txt`: tells LLMs how to use `/search.txt` and `/src/`, when to
  import a result, and when to copy and change its code.

A new query costs one query embedding, approximately $0.0000003, so the server
has no rate limit and no budget. A query longer than 200 characters gets HTTP
400. An invalid parameter also gets HTTP 400, with a short plain-text message.
When OpenRouter does not answer, the server returns HTTP 502. The server
keeps the rankings of the 256 most recent queries in memory, so the next 20
results do not need a new ranking.

Every response has a strict Content-Security-Policy (only the style and the
script of the page, identified by their hashes), `X-Content-Type-Options`,
`Referrer-Policy` and `Strict-Transport-Security`. `/robots.txt` keeps
crawlers away from the HTML result pages. It allows `/search.txt`, `/src/`
and `/llms.txt`, because some LLM tools do not fetch a URL that `robots.txt`
disallows.

## Docker

The image holds the code only. Mount the data directory (with `index/`) at
`/data`. The server writes its query vectors to `queries.sqlite` in that
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
- `data/`: `index/` and `queries.sqlite`, owned by user 10001 (the user in
  the image)

The server keeps only the text vectors (55 MB) and three numbers for each
document in memory. It reads the documents, the BM25 postings and the
signature vectors from disk for each query. The process uses approximately
150 MB of memory. The host has a 2 GB swap file.

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
benchmark is only for evaluation: do not train on it or tune on it.

Measure the search:

```sh
python -m jend.evaluate --label baseline
```

The query vectors come from `data/hub/queries.sqlite`, so a second
run costs nothing and gives the same result. The command writes the run to
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
