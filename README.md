# JendHub

JendHub is a search engine for Bend definitions. The corpus is every package
on [BendHub](https://hub.bend-lang.com) and the latest released `base.bend`.
AI agents are the main users, and they read plain text. People see the same
data as web pages. Both are on one site, https://jend.leeeet.dev.

## Design decisions

- **Users:** AI agents first. Agents get plain text with full URLs, because
  they read text more easily than JSON, and most fetch tools cannot ask for a
  format. People get the same data as HTML pages with no JavaScript. One
  service serves both forms, because they are one product with one life cycle.
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
  only paid cost for each search request is the query embedding. The reranker
  is a small LightGBM model of cheap features. It runs in the site and calls no
  model API.
- **Services:** each job with its own life cycle is a service with an HTTP
  contract. A new service is a new package under `services/`.
- **Environments:** development and production are decoupled. Development
  runs with Nix and no Docker. Production runs the Docker image of
  `deploy/Dockerfile` and no Nix. Each pins its own dependency versions: the
  dev shell uses nixpkgs, and the image uses `uv.lock`. No data moves between
  them, so each environment builds its own index.

## Services

| Service | Job | State |
|---|---|---|
| `syncer` | Stores the new packages of BendHub and each new Base release. Queues an index job for each new latest release. Promotes a finished generation and deletes retired ones. | none |
| `worker` | Runs the index jobs: extracts the definitions of a release, resolves its uses with the Rust indexer, computes the rerank features, embeds the documents and writes Postgres and Qdrant. Scale it out with more replicas. | a local copy of the package files |
| `site` | The public site. Runs the search (embeds the query, gets candidates from Qdrant, and reranks them), and answers people with HTML pages and agents with plain text. | none |

Postgres and Qdrant are the only stores.

```
BendHub ──> syncer ──> Postgres <── worker ──> Qdrant
                          ▲                      ▲
                          └─────── site ─────────┘
                                    ▲
                 AI agents, people ─┘
```

### Paths

| For agents (plain text) | For people (HTML) | Answer |
|---|---|---|
| `GET /llms.txt` | `GET /` | How to use JendHub. The home page also shows the size of the live index. |
| `GET /search.txt?q=<query>&limit=10&offset=0` | `GET /search?q=<query>&offset=0` | The best definitions with their import line and the name to call. `limit` is 1 to 50 (the pages show 20). The ranking holds at most 50 results, and `offset` pages through it. |
| `GET /src/<release>/<path>?def=<name>` | `GET /definitions/<id>` | A definition with the import lines that its code uses, and the definitions that it uses. |
| `GET /src/<release>/<path>` | `GET /files/<release>/<path>` | A file of any stored release, also an invalid or unindexed one. `<release>` is `name@version`, the hash of the package, or `Base`. Each import line names an exact release, so the file loads from any project. The page links each import to its file and each declaration to its definition. `#L<n>` marks one line, and `?lines=<first>-<last>` marks a range. |
| `GET /src/<release>/` | `GET /files/<release>` | The files of a release. The page also shows the number of indexed definitions in each file. |
| `GET /packages/<name>.txt` | `GET /packages/<name>` | Each release of a package, newest first, with its state (`indexed`, `invalid` with its load errors, or `unindexed`), and one page of the definitions of the indexed release. The page groups the definitions by file. |
| `GET /status.txt` | `GET /status` | The generations of the live corpus with their sizes and job counts, and one page of the invalid releases with their load errors, newest first. |
| | `GET /opensearch.xml` | The OpenSearch description, so that a browser can search JendHub from its address bar. Each page links to it. |

A text answer is plain text, also an error. Its links are full URLs, and a
paged answer ends with the URL of the next page. Each answer stays short,
because it goes into the context of an agent: a head of more than 12 lines
ends with `…`, and a load error names at most 5 of its files.

```
1. Json.parse (https://jend.leeeet.dev/src/bend-net-json@0.3.0.0/json.bend?def=parse)
   import bend-net-json@0.3.0.0/json.bend as Json
   # One JSON text: a value with optional whitespace around it.
   def parse(+s: String) -> Maybe<&2, Val>:
```

A package definition is imported from its exact release. Its alias is the
module name in PascalCase, or the package name for a `main` module. A Base
definition needs only `import Base`. A file of a release gets its relative
imports and its hash imports written as `name@version` paths. Each page links
to its text form with `<link rel="alternate" type="text/plain">`.

Each client IP gets at most 30 searches and 600 requests a minute, with a
burst of 10 searches and 120 requests. More get `429 Too Many Requests` with
`Retry-After`. Behind the Cloudflare Tunnel, the client IP is the
`CF-Connecting-IP` header.

Every service has `/healthz` (the process runs) and `/readyz` (it can do its
job) on its port, and `/metrics` for Prometheus on the internal port 9000 only.
The services log JSON lines. Each response has `X-Content-Type-Options`,
`Referrer-Policy`, `Strict-Transport-Security`, and a strict
Content-Security-Policy that allows no script. `/robots.txt` keeps crawlers
away from the search pages, and allows the rest, because some LLM tools do not
fetch a URL that `robots.txt` disallows.

### Index generations

A generation is one complete index for one corpus, one Base tag, one indexer
version and one vector setting. Its definitions are rows in Postgres, and its
vectors are points in one Qdrant collection, with a code vector and a name
vector for each definition. Search reads the vector setting of
the live generation from Postgres.

- **A new package release:** the syncer queues one job. The worker indexes the
  release and removes the older release of the same name in the same
  generation. Search sees the change at once. An older release never replaces
  a newer one.
- **An invalid release:** if a file of the release does not load, the worker
  writes nothing for it, and records it in `invalid_releases` with the load
  error of each file that does not load. A load error is what the loader
  expected and the file and line where the load stopped. That file can be in
  the release, in another package or in Base. Often many files stop at one
  line of a module that they all import, so the pages show each error once,
  with the places where the loads stopped and the files that do not load.
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
  job also fails when its worker stops, for example out of memory, during each
  of its 5 attempts. A generation with a failed job does not go live.
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

Each definition has a second vector, the name vector: the embedding of the
package name and the module path, then the name, for example
`bend-time-lib date` and `Date.add_days`. It tells apart the functions of one
module, which share most of their code vocabulary.

1. Qdrant returns the 50 nearest definitions by the code vector, with the
   release and the module of each. Both vectors have 2,048 dimensions with
   int8 quantization in RAM, and Qdrant scores with the int8 vectors only. The
   full vectors stay on disk for a rebuild. The name vectors have no HNSW
   graph, because the engine only scores the candidates with them.
2. The engine reads the 50 from Postgres, and asks Qdrant for their name
   scores and for the code scores of their parents and children outside the
   50.
3. It computes 34 features for each of the 50 and sorts them with a LambdaMART
   model (LightGBM). `search/ranking.py` defines the features. The model is
   `search/ranker.txt`.

| Group | Features |
|---|---|
| Embeddings | The cosine of the code vector and of the name vector. |
| Words | The share of the words that the query and the name have in common, in three forms. |
| Signature | The share of the parameter and result types that the query and the head have in common. |
| Uses | The users in its release; the use by another release; the doc comment; the helper flag. A helper is a definition that a definition of its module reaches, when that name followed by `.` starts its name, as `Map.put.go` under `Map.put`. |
| Shape | Type, law, Base, the length of the name and of the code. |
| Neighbors | The best similarity in the same module and in the same package among the 50, the similarity of the parent (`Date` for `Date.add_days`) and of the children, each minus its own. |

The embeddings, the words and the signature also give two values relative to
the 50 candidates: the value minus the best value, and the z-score.

A use of an older release of a package counts for the same module and name in
the latest release. `jendhub bench train` trains the model on all queries of
the benchmark and writes `search/ranker.txt`. Train it again after a change of
the features, the queries or the judgments. See
[bench/README.md](bench/README.md) for the measurements behind each choice.

The site stores every search of agents and people in the `searches` table, to improve the search with real queries later. It writes the
row after the answer, so the log adds no latency, and a failed write does not
fail the search. The row holds the whole ranking, not only the page, and no
IP address.

The first live index holds 56,650 definitions: Base and the latest valid
release of 106 packages. On the server, the engine answers a query in 54 to
75 ms (median) and 133 to 187 ms (p95) when its embedding is in the cache of
the site, measured in four passes over 30 benchmark queries. The server also
runs other services and uses swap, so the values change from pass to pass. The
name scores are the largest part, about 90 ms, and they run in parallel with
the Postgres read. A
new query waits 0.8 to 4 s for the embedding from OpenRouter, which is the
largest part of the latency. The site waits at most 10 s for an embedding and
tries once more, so a slow OpenRouter gives an error and not a search that
hangs. The worker waits up to 120 s and tries 6 more times.

## Development

Clone with the submodule, and put `OPENROUTER_API_KEY` in `.env`. `nix develop`
loads it, and points `JENDHUB_DATABASE_URL` and `JENDHUB_QDRANT_URL` at the dev
services.

```bash
git clone --recurse-submodules <url>
nix develop -c process-compose up
```

This starts Postgres, Qdrant, the migrations and `site`, in this order. Then
open http://127.0.0.1:8080, and http://127.0.0.1:8080/llms.txt for the agent
form.

Postgres and Qdrant come from nix and run as normal processes, with their data
in `data/postgres` and `data/qdrant`. Search needs a live index in that data.
Without one, the pages answer that the index is not ready.

`syncer` and `worker` are off by default, because they call BendHub and
OpenRouter on their own. Start one with `process-compose process start
<name>`. For the CLI, open a second terminal with `nix develop`, and run
`jendhub --help`.

### The production stack on your machine

`nix run .#stack` builds the production image and runs it with
`deploy/compose.yaml`, as a deploy does. Use it to check a change before a
deploy. It needs Docker, and it uses its own volumes, the port 18080 for the
site and 19090 for Prometheus, so it can run next to `process-compose up`.

```bash
nix run .#stack -- up        # build the image, then start everything
nix run .#stack -- down      # stop; the volumes stay
```

The stack never gets data from the dev stores. `up` refuses to start a stack
with no live index, because the syncer and the worker would then build a full
index and pay OpenRouter for each embedding. `up --fresh` does that on
purpose. When the stack runs, its syncer and worker index each new BendHub
release, as in a deploy. Any other argument goes to `docker compose`, for
example `nix run .#stack -- logs -f site`, or
`nix run .#stack -- run --rm migrate index status` for a `jendhub` command.

### The image

`deploy/Dockerfile` builds one image, `jendhub`, for all services. Its first
stage compiles the Rust indexer. Its second stage installs the Python
dependencies with `uv sync --frozen`, from `pyproject.toml` and `uv.lock`. The
last stage is `python:3.14-slim` with the dependencies, the indexer and
`src/jendhub`. It also installs `libgomp1`, because LightGBM needs OpenMP. The
compose file gives each service its command.

The dev shell does not read `uv.lock`. When you add a dependency, add it to
`flake.nix` and to `pyproject.toml`, then run `uv lock`.

`nix flake check` runs all checks: the Python tests, which start their own
Postgres and Qdrant, Ruff, Pyright and the Rust lints.

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

`python -m jendhub.services <site|syncer|worker> [--host H] [--port P]
[--metrics-port M]` runs a service. In the dev shell, `jendhub-service` does the
same.

### Settings

All settings come from the environment.

| Variable | Used by | Default |
|---|---|---|
| `JENDHUB_DATABASE_URL`, `JENDHUB_DATABASE_POOL_SIZE` | all | required, 10 |
| `JENDHUB_QDRANT_URL`, `JENDHUB_QDRANT_API_KEY` | all | required, none |
| `OPENROUTER_API_KEY` | `site`, `worker`, the benchmark | required |
| `JENDHUB_SITE_URL` | `site`: its public URL, for the full URLs in the text answers | required |
| `JENDHUB_CLIENT_IP_HEADER` | `site`: the header that holds the client IP; without it, the IP of the connection | none |
| `JENDHUB_SEARCHES_PER_MINUTE`, `JENDHUB_SEARCH_BURST` | `site`: the search limit of each client IP | 30, 10 |
| `JENDHUB_REQUESTS_PER_MINUTE`, `JENDHUB_REQUEST_BURST` | `site`: the request limit of each client IP | 600, 120 |
| `JENDHUB_QUERY_CACHE_SIZE`, `JENDHUB_TARGET_REFRESH_SECONDS` | `site` | 10,000, 10 |
| `JENDHUB_STATUS_SECONDS` | `site`: the time it keeps the sizes of the generations | 30 |
| `JENDHUB_SYNC_INTERVAL_SECONDS` | `syncer` | 300 |
| `JENDHUB_LIBRARY_PATH`, `JENDHUB_WORKER_CONCURRENCY` | `worker` | `library`, 2 |

## Deployment

The production site is https://jend.leeeet.dev. It runs on one server with
`deploy/compose.yaml`: Postgres 17, Qdrant 1.18.2, `site`, `syncer`, `worker`,
Prometheus and a Cloudflare Tunnel. No port is open to the internet:
`cloudflared` connects out to Cloudflare, and Cloudflare sends the requests for
`jend.leeeet.dev` to `http://site:8000`. The JendHub services run as an
unprivileged user. Each container has a memory limit, and
Docker keeps at most 3 log files of 10 MB for each container. The server has 1
CPU and 1.6 GiB of RAM, so it needs a swap file: without one, the page cache
cannot hold Qdrant's vectors, and the restore of the vectors fails. It has a 4
GB `/swapfile` with `vm.swappiness=10`.

`nix run .#deploy` deploys from your machine. Nix runs only on your machine:
the server needs only Docker. The deploy needs SSH access to the server and
`OPENROUTER_API_KEY` and `CLOUDFLARE_TUNNEL_TOKEN` in `.env`:

```bash
nix run .#deploy                 # build, copy and start the current version
nix run .#deploy -- logs site    # any other argument goes to docker compose
```

A deploy builds the image with `docker build`, streams it over SSH into the
server's Docker, copies the compose file and the Prometheus configuration to
`/opt/jendhub`, and writes `/opt/jendhub/.env` (mode 600) with the secrets. It
keeps the Postgres password of the server, or makes one on the first deploy.
Then `docker compose up --wait` applies the migrations and starts the new
version, and the previous image is deleted. Like `nix run .#stack -- up`, it
refuses to start without a live index. `up --fresh` lets the server build its
own index. Set `JENDHUB_DEPLOY_TARGET` (default `root@195.63.145.165`) and
`JENDHUB_DEPLOY_DIRECTORY` (default `/opt/jendhub`) for another server.

Prometheus scrapes the metrics port of each service every 30 s and keeps 30
days. It has no alert rules. It listens only on the server's loopback address:

```bash
ssh -L 9090:127.0.0.1:9090 root@195.63.145.165   # then open http://127.0.0.1:9090
```

## Data

| Store | Content |
|---|---|
| Postgres `packages`, `package_files`, `rejected_packages`, `base_releases` | The mirror of BendHub and of each Base release. |
| Postgres `generations`, `index_jobs` | The index generations with their vector settings, and the job queue. |
| Postgres `invalid_releases` | The releases that never go into the index, with the load error of each file that does not load. |
| Postgres `definitions`, `uses`, `external_uses` | The definitions of each generation, with their features and their uses. |
| Postgres `embeddings` | The embedding cache: full float32 vectors, keyed by model and text hash. |
| Postgres `judgments`, `enrichments`, `enrichment_rejections` | The benchmark grades and the enrichments. |
| Postgres `searches` | Every real search: time, client (`agents` for the text form, `web` for the pages), the caller's User-Agent, query, page, generation, the whole ranking and the search time. JendHub never deletes a row. |
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
| `lab` | Experimental pipelines that run in memory: BM25, dense search, fusion, reranking and the learned ranker. |
| `bench` | The benchmark: queries, judgments, the judge, pooling and the measures. |
| `services` | The HTTP services: `site` with its pages, text answers and rate limit, `syncer` and `worker`. |
| `cli` | The admin commands. |

## External models

| Job | Model | Provider | Notes |
|---|---|---|---|
| Embeddings | `qwen/qwen3-embedding-8b`, 4096 dimensions | Nebius, pinned | All cached vectors must come from one provider. |
| Enrichment | `deepseek/deepseek-v4-flash-0731`, no reasoning | `deepinfra/fp8`, then `parasail/fp8` and `digitalocean` | Only for lab pipelines. |
| Benchmark judge | `openai/gpt-6-luna`, medium reasoning | OpenAI, pinned | |

The pin keeps the vectors of queries and definitions comparable. OpenRouter
also offers DeepInfra and SiliconFlow (fp8) for this model. A test embedded the
500 benchmark queries with each and ranked them against the Nebius index.
SiliconFlow vectors have a cosine of 0.9999 to the Nebius vectors and lose
0.001 nDCG. DeepInfra vectors have a cosine of 0.997 and lose 0.008, and only
96% of the queries keep their first result.

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
