# JendHub

JendHub is a search engine for Bend definitions. The corpus is every package
on [BendHub](https://hub.bend-lang.com) and the latest released `base.bend`.
AI agents are the main users. They read plain text from the agent interface.
People see the same data in a web app.

## Design decisions

- **Users:** AI agents first. The agent interface answers in plain text with
  full URLs, because agents read text more easily than JSON, and most fetch
  tools cannot ask for a format. The web app shows the same data to people, and
  it has no data of its own.
- **Corpus:** the store keeps every version of every package, because packages
  import each other by hash. Search uses only the latest valid version of each
  named package, as if other versions do not exist.
- **Valid releases:** a release is valid if each of its files loads with the
  current Base, as the compiler loads it. If one file does not load, JendHub
  drops the whole release and never tries it again, also with a later Base.
  Search then uses the latest valid release of the same name.
- **Base:** JendHub keeps each released `base.bend`. `import Base` always means
  the latest release. A new Base release rebuilds the index.
- **Definitions:** a definition is a top-level `type`, `def` or `law`. A
  constructor is not a definition: a search for a constructor finds its type.
  A `def` that fills a law is not a definition: the law is.
- **Definition IDs:** a Base definition has its bare name, for example
  `List.map`. A package definition has `name@version/module:Name`, for example
  `bend-kit-json@0.5.0.1/json:parse.bytes`. IDs do not contain `.bend`.
- **Scale:** the design must hold millions of definitions. No service keeps
  the index in memory. Postgres is the source of truth. Qdrant holds the
  vectors as a projection that can be built again from Postgres.
- **Models:** all models run through [OpenRouter](https://openrouter.ai). The
  only paid cost for each search request is the query embedding. JendHub uses
  no reranker model. The reranker is a weighted sum of cheap features.
- **Services:** each job with its own life cycle is a service with an HTTP
  contract. A new service is a new package under `services/`.

## Services

| Service | Job | State |
|---|---|---|
| `syncer` | Stores the new packages of BendHub and each new Base release. Queues an index job for each new latest release. Promotes a finished generation and deletes retired ones. | none |
| `worker` | Runs the index jobs: extracts the definitions of a release, resolves its uses with the Rust indexer, computes the rerank features, embeds the documents and writes Postgres and Qdrant. Scale it out with more replicas. | a local copy of the package files |
| `search` | The search engine. Embeds the query, gets candidates from Qdrant and from an exact-name index, and reranks them. Internal. | none |
| `agents` | The agent interface. Validates requests, calls `search`, and answers in plain text. | none |
| `web` | The web app for people. A SvelteKit server that renders each page from the JSON of `search`. It sends no JavaScript to the browser. | none |

Postgres and Qdrant are the only stores.

```
BendHub ──> syncer ──> Postgres <── worker ──> Qdrant
                          ▲                      ▲
                          └────── search ────────┘
                                  ▲   ▲
            AI agents ──> agents ─┘   │
               people ──> web ────────┘
```

### The agent interface

| Request | Answer |
|---|---|
| `GET /search?q=<query>&limit=10&offset=0` | The best definitions: ID, kind, name, package, version, module, head, score, the import line, the name to call, and the file. `limit` is 1 to 50. The ranking holds at most 50 results, and `offset` pages through it. |
| `GET /definitions/<id>?used_by_offset=0&used_by_limit=100` | The text of a definition, its lines, the import lines that its code uses, the definitions that it uses, and one page of the definitions that use it. |
| `GET /files/<release>/<path>` | A file of any stored release, also an invalid or unindexed one. `<release>` is `name@version`, the hash of the package, or `Base`. Each import line names an exact release, so the file loads from any project. |
| `GET /files/<release>` | The paths of the files of a release. |
| `GET /packages/<name>?definitions_offset=0&definitions_limit=100` | Each release of a package, newest first, with its state (`indexed`, `invalid` with the reason, or `unindexed`), and one page of the definitions of the indexed release. |
| `GET /status?invalid_releases_offset=0&invalid_releases_limit=100` | The generations of the live corpus with their sizes and job counts, and one page of the invalid releases, newest first. |
| `GET /llms.txt` | How an agent uses the interface, and when it imports a result or copies its code. |

Each answers in plain text, also an error. The links are full URLs, and a
paged answer ends with the URL of the next page:

```
1. Json.parse (bend-net-json@0.3.0.0/json:parse)
   import bend-net-json@0.3.0.0/json.bend as Json
   # One JSON text: a value with optional whitespace around it.
   def parse(+s: String) -> Maybe<&2, Val>:
   source: https://<agents>/definitions/bend-net-json@0.3.0.0/json:parse
```

A package definition is imported from its exact release. Its alias is the
module name in PascalCase, or the package name for a `main` module. A Base
definition needs only `import Base`. A file of a release gets its relative
imports and its hash imports written as `name@version` paths.

Every service also has `/healthz` (the process runs), `/readyz` (it can do its
job) and `/metrics` (Prometheus). The services log JSON lines. Each response
has `X-Content-Type-Options`, `Referrer-Policy`, `Strict-Transport-Security`,
and a strict Content-Security-Policy. `/robots.txt` allows all of the agent interface,
and it keeps crawlers away from the search pages of the web app, because some LLM
tools do not fetch a URL that `robots.txt` disallows.

### Index generations

A generation is one complete index for one corpus, one Base tag, one indexer
version and one vector setting. Its definitions are rows in Postgres, and its
vectors are points in one Qdrant collection. Search reads the vector setting of
the live generation from Postgres.

- **A new package release:** the syncer queues one job. The worker indexes the
  release and removes the older release of the same name in the same
  generation. Search sees the change at once. An older release never replaces
  a newer one.
- **An invalid release:** if a file of the release does not load, the worker
  writes nothing for it, and records it in `invalid_releases` with the reason.
  The worker then queues the next older valid release of the same name, and
  the syncer never queues the invalid one again.
- **A new Base release, a new `INDEXER_VERSION`, or a new vector setting
  (`PRODUCTION` in `vectors/index.py`):** the syncer creates a new generation
  and queues a job for Base and for each latest release. The worker gets the
  vectors of unchanged documents from the embedding cache, so a new vector
  setting pays for no embedding. The old
  generation stays live until the new one has no unfinished job. Then the new
  one goes live, and the old one is deleted after 10 minutes.
- **A failed job** waits 1, 2, 4, 8 minutes and so on, at most 1 hour. After 5
  attempts it stops, and `jendhub index retry <generation>` queues it again. A
  generation with a failed job does not go live.
- **A lost Qdrant collection:** `jendhub index restore-vectors` writes it again
  from the embedding cache, with no new embeddings.

The job queue is a Postgres table. A worker claims a job with
`FOR UPDATE SKIP LOCKED` and a lease of 15 minutes. If a worker stops, another
worker takes the job when the lease ends.

## The search

The search engine embeds the query with the instruction "Given a programming
task, retrieve the Bend library function or type that a programmer calls to do
it". Each document is the package name and the module path (`Base` for Base),
then the code, cut at 32,000 characters. The cut stays far below the 40,960
tokens of the embedding model, and it changes 15 of 146,033 definitions of all
named releases, all of them generated proofs.

1. Qdrant returns the 1,000 nearest definitions. The vectors have 2,048
   dimensions with binary quantization in RAM. Qdrant rescores the candidates
   with the full vectors on disk.
2. Definitions whose name or ID is the query become candidates too.
3. The engine reads the features of the top 50 and the name candidates from
   Postgres, and sorts them by this weighted sum:

| Weight | Feature |
|---|---|
| 1.0 | Cosine similarity |
| 0.15 | Name overlap |
| 0.2 | Signature match: the share of the parameter and result types that the query and the head have in common |
| 0.15 | The best similarity among the definitions that the definition reaches, minus its own. A reached definition outside the 1,000 counts as the lowest similarity of the 1,000. |
| 0.075 | 1 if no definition of its release uses it |
| 0.02 | ln(1 + the number of its users in its release) |
| 0.02 | 1 if a definition of another release uses it |
| 0.015 | 1 if it has a doc comment |
| -0.04 | 1 if a definition of its module reaches it, and that name followed by `.` starts its name, as `Map.put.go` under `Map.put` |

A use of an older release of a package counts for the same module and name in
the latest release. On the dev benchmark, nDCG@10 is 0.816 with each result
judged, against 0.819 before the scale changes. See
[bench/README.md](bench/README.md) for the measurements behind each choice.

The first live index holds 56,650 definitions: Base and the latest valid
release of 106 packages. A query answers in 20 to 70 ms when its embedding is
in the cache of the search service. A new query waits 0.8 to 4 s for the
embedding from OpenRouter, which is the largest part of the latency.

## Development

Clone with the submodule, and put `OPENROUTER_API_KEY` in `.env`. `nix develop`
loads it, and points `JENDHUB_DATABASE_URL` and `JENDHUB_QDRANT_URL` at the dev
services.

```bash
git clone --recurse-submodules <url>
nix develop -c process-compose up
```

This starts Postgres, Qdrant, the migrations, `search`, `agents` and `web`, in
this order. Then open http://127.0.0.1:8090. The agent interface is at
http://127.0.0.1:8080. The `web` process runs `npm install` in `web` before it
starts the Vite dev server, so the first start takes longer.

Postgres and Qdrant come from nix and run as normal processes, with their data
in `data/postgres` and `data/qdrant`. Search needs a live index in that data.
Without one, the pages answer that the index is not ready.

`syncer` and `worker` are off by default, because they call BendHub and
OpenRouter on their own. Start one with `process-compose process start
<name>`. For the CLI, open a second terminal with `nix develop`, and run
`jendhub --help`.

### The production stack on your machine

`nix run .#stack` runs the production images with `deploy/compose.yaml`, as a
deploy does. Use it to check a change before a deploy. It needs Docker, and it
uses its own volumes and the ports 18090 (web) and 18080 (agents), so it can run
next to `process-compose up`.

```bash
nix develop -c process-compose up postgres qdrant database   # the dev stores
nix run .#stack -- seed      # copy the dev index into the stack, once
nix run .#stack -- up        # build and load the images, then start everything
nix run .#stack -- down      # stop; the volumes stay
```

`seed` copies the dev database into the stack, and then writes the Qdrant
collections from the embedding cache with `jendhub index restore-vectors`. It
pays for no embedding, and it takes about 5 minutes. `up` refuses to start a
stack with no live index, because the syncer and the worker would then build a
full index and pay OpenRouter for each embedding. `up --fresh` does that on
purpose. When the stack runs, its syncer and worker index each new BendHub
release, as in a deploy. Any other argument goes to `docker compose`, for
example `nix run .#stack -- logs -f web`.

The web app gets its types from the OpenAPI schema of `search`. In `web`,
`npm run generate` reads the schema from `jendhub-service search --openapi`, so
it needs the dev shell. The nix build of `web` reads the schema from the Python
source. Thus the types always match `search`, and no copy of the schema is in
git.

`nix flake check` runs all checks: the Python tests, which start their own
Postgres and Qdrant, Ruff, Pyright, the Rust lints and the web checks. In `web`, `npm run check`
type-checks, `npm run lint` runs Prettier and ESLint, and `npm test` builds the
app and tests the pages against a stub of `search`.

## Commands

`--data` sets the directory of the local package copy and of the corpus text.
The default is `data`.

| Command | What it does |
|---|---|
| `jendhub migrate` | Applies the database migrations. |
| `jendhub sync [--dry-run]` | Stores the new packages of BendHub and the latest released `base.bend`. `--dry-run` prints the plan. |
| `jendhub dev select (--packages N \| --definitions N)` | Selects the hottest release of each named package into `bench/dev.json`. |
| `jendhub enrich` | Writes search metadata for each definition of the dev corpus. |
| `jendhub index status` | Shows each generation and the states of its jobs. |
| `jendhub index schedule` | Does one cycle of the syncer without the sync: queues jobs and promotes. |
| `jendhub index retry GENERATION` | Queues the failed jobs of a generation again. |
| `jendhub index restore-vectors` | Writes the Qdrant collection of each generation that Qdrant does not have, from the embedding cache. It pays for no embedding. |
| `jendhub bench ...` | Runs the benchmark. See [bench/README.md](bench/README.md). |

`python -m jendhub.services <agents|search|syncer|worker> [--host H] [--port P]`
runs a service, and `--openapi` prints its OpenAPI schema. In the dev shell,
`jendhub-service` does the same. `node web/build` runs the web app after
`npm run build`.

### Settings

All settings come from the environment.

| Variable | Used by | Default |
|---|---|---|
| `JENDHUB_DATABASE_URL` | `search`, `syncer`, `worker` | required |
| `JENDHUB_DATABASE_POOL_SIZE` | `search`, `syncer`, `worker` | 10 |
| `JENDHUB_QDRANT_URL`, `JENDHUB_QDRANT_API_KEY` | `search`, `syncer`, `worker` | required, none |
| `OPENROUTER_API_KEY` | `search`, `worker`, the benchmark | required |
| `JENDHUB_SEARCH_URL` | `agents`, `web` | required |
| `JENDHUB_SYNC_INTERVAL_SECONDS` | `syncer` | 300 |
| `JENDHUB_LIBRARY_PATH`, `JENDHUB_WORKER_CONCURRENCY` | `worker` | `library`, 2 |
| `JENDHUB_QUERY_CACHE_SIZE`, `JENDHUB_TARGET_REFRESH_SECONDS` | `search` | 10,000, 10 |
| `JENDHUB_STATUS_SECONDS` | `search`: the time it keeps the sizes of the generations | 30 |
| `JENDHUB_AGENTS_URL` | `agents` and `web`: the public URL of the agent interface, for the links in its answers and on the web pages | required |
| `HOST`, `PORT` | `web` | `0.0.0.0`, 3000 (8000 in the image) |

## Deployment

`nix build .#image-<service>` builds one OCI image for each service. Each image
runs as an unprivileged user and holds no shell. The `web` image holds Node.js
and the built app. `deploy/compose.yaml` runs the
whole system with Postgres 17 and Qdrant 1.18.2:

```bash
for service in agents search syncer worker web; do
  docker load < "$(nix build .#image-$service --no-link --print-out-paths)"
done
POSTGRES_PASSWORD=... OPENROUTER_API_KEY=... docker compose -f deploy/compose.yaml up -d
```

The `migrate` container applies the migrations before the services start. Only
`search` and `worker` get the OpenRouter key. Only `agents` and `web` publish a
port: 8080 and 8090 by default, set `JENDHUB_AGENTS_PORT` and `JENDHUB_WEB_PORT`.
Set `JENDHUB_AGENTS_URL` to the public URL of the agent interface. The default
is `http://127.0.0.1:<JENDHUB_AGENTS_PORT>`.

## Data

| Store | Content |
|---|---|
| Postgres `packages`, `package_files`, `rejected_packages`, `base_releases` | The mirror of BendHub and of each Base release. |
| Postgres `generations`, `index_jobs` | The index generations with their vector settings, and the job queue. |
| Postgres `invalid_releases` | The releases that never go into the index, with the reason. |
| Postgres `definitions`, `uses`, `external_uses` | The definitions of each generation, with their features and their uses. |
| Postgres `embeddings` | The embedding cache: full float32 vectors, keyed by model and text hash. |
| Postgres `judgments`, `enrichments`, `enrichment_rejections` | The benchmark grades and the enrichments. |
| Qdrant `jendhub-g<generation>-<setting>` | The vectors of one generation. |
| `data/library` | The worker's copy of the package files in the `BEND_LIB` layout, for the Rust indexer. |

The sync rejects a release with an invalid name or version, or a package with
an invalid file name, and never tries it again. A hash mismatch, a network
error or a listing without a valid hash stops the sync.

## Uses

The use graph tells which definitions use which other definitions. A use is
any name that resolves to a definition: a call, a type, a constructor, or a use
with no name of its own, such as `T.add` behind `(a + b : T)`. A constructor
stands for its type.

JendHub resolves names exactly as the Bend compiler does. Our fork of the
[Bend 2 language server](https://github.com/IlyaGulya/bend2-lsp-rs), the
submodule `bend2-lsp-rs`, holds a port of the compiler's front end (Bend
v2.0.37): its loader, parser and match flattening. Its `tools/oracle` compares
the port with the compiler itself. On every file of the mirror and of the
compiler's repository, the parsed terms are byte-identical, and every
identifier token has the meaning that the compiler gives it.

The `jendhub-index` program in `indexer/` loads each file of a release as the
compiler loads the file it checks. It writes the uses and the declared names
as JSON. The uses inside one release are the graph of that release. A use of
another release is stored by the module and name of its target, without the
version.

Python extracts the definitions from the source text, with their doc comments
and heads. In each of the 2,026 files of the latest releases that load, the
extraction and the compiler declare the same names. The worker checks this for
each file and logs each difference.

With Base v2.0.37, 46 files of 7 latest releases do not load, so these
releases are invalid: 4 declare a constructor that Base declares too (`Ready`
or `Wait`), 2 use a name part that starts with a digit, and 1 imports a
package that BendHub does not have. The loader parses and resolves names, but
does not check types, so a valid release can still have type errors.

## Code layout

The code is in `src/jendhub`. Each package uses only the packages above it in
this table. The tests in `tests` have the same layout.

| Package | Job |
|---|---|
| top level | Errors, the JSON type, settings and logging. |
| `db`, `remote` | The Postgres pool and migrations; the HTTP client and OpenRouter. |
| `hub` | Read BendHub and GitHub, store packages, and keep the local package copy. |
| `corpus` | Extract definitions from source files, and select the dev corpus. |
| `uses` | The use graph and the call to the Rust indexer. |
| `embedding` | The embedding model, the remote embedder and the caches. |
| `enrich` | The enricher, its windows and its store. |
| `vectors` | The Qdrant collections and the vector settings. |
| `search` | The production search: tokens, signatures, documents, the engine and the definition reader. |
| `indexing` | The artifacts of a release, the index store, the worker, the scheduler and the restore of vectors. |
| `lab` | Experimental pipelines that run in memory: BM25, dense search, fusion and reranking. |
| `bench` | The benchmark: queries, judgments, the judge, pooling and the measures. |
| `services` | The HTTP services and their contracts. |
| `cli` | The admin commands. |

## External models

| Job | Model | Provider | Notes |
|---|---|---|---|
| Embeddings | `qwen/qwen3-embedding-8b`, 4096 dimensions | Nebius, pinned | All cached vectors must come from one provider. |
| Enrichment | `deepseek/deepseek-v4-flash-0731`, no reasoning | `deepinfra/fp8`, then `parasail/fp8` and `digitalocean` | Only for lab pipelines. |
| Benchmark judge | `openai/gpt-6-luna`, medium reasoning | OpenAI, pinned | |

Queries get the Qwen3 instruction format: `Instruct: <task>\nQuery:<query>`.
Documents get no instruction.

### Enrichment

The enricher writes a summary, 3 to 5 search queries and a role (`api` or
`helper`) for each definition of the dev corpus. The production search does
not use it. Each request holds a window of one source file: 40 definitions
before the targets, 40 targets and 20 definitions after them. The window keeps
the source lines between definitions, so the model sees comments, the fillings
of laws and the places that use each target. A limit of 30,000 characters on
each side keeps very large definitions out of the context.

Each window gets one request. If the answer is invalid, the store remembers the
window as rejected, and `jendhub enrich` never sends it again. A failed request
is not remembered: `jendhub enrich` stops with an error, and the next run sends
the window again.
