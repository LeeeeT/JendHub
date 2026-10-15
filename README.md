# JendHub

JendHub is a search engine for Bend definitions. The corpus is every package
on [BendHub](https://hub.bend-lang.com) and the latest released `base.bend`.
AI agents are the users. They will search through a JSON or text API. Today
JendHub has only a CLI.

## Design decisions

- **Users:** AI agents, not people. A result is a definition ID.
- **Corpus:** the mirror keeps every version of every package, because
  dependencies are transitive. Search will use only the latest version of each
  package, as if older versions do not exist. Search ignores dependencies.
- **Base:** JendHub keeps only the latest released `base.bend`. `import Base`
  always means the latest release.
- **Definitions:** a definition is a top-level `type`, `def` or `law`. A
  constructor is not a definition: a search for a constructor finds its type.
  A `def` that fills a law is not a definition: the law is.
- **Definition IDs:** a Base definition has its bare name, for example
  `List.map`. A package definition has `name@version/module:Name`, for example
  `bend-kit-json@0.5.0.1/json:parse.bytes`. IDs do not contain `.bend`.
- **Models:** all models run through [OpenRouter](https://openrouter.ai).
  The only paid cost for each search request is the query embedding. JendHub
  uses no reranker model. Rerankers are weighted sums of cheap features.
- **Composability:** each search method is a pipeline, and pipelines combine
  freely. The benchmark compares any two pipelines. Keep experiments as
  separate pipelines.

## Setup

Clone with the submodule, and put `OPENROUTER_API_KEY` in `.env`. `nix develop`
loads it automatically.

```bash
git clone --recurse-submodules <url>
nix develop
jendhub --help
```

Run the checks:

```bash
nix flake check
```

## Commands

Run the commands in this order. `--data` sets the data directory. The default
is `data`.

`--top N` makes `enrich` and `bench` use only Base and the N hottest packages of
the dev corpus, as if the dev corpus held only them. The order of
`bench/dev.json` is the hot order. For example, `jendhub --top 10 bench run
<pipeline>` scores a pipeline on a smaller corpus.

| Command | What it does |
|---|---|
| `jendhub sync [--dry-run]` | Mirrors BendHub and the latest released `base.bend` into `data/full`. `--dry-run` prints the plan. |
| `jendhub dev select (--packages N \| --definitions N)` | Selects the hottest release of each named package into `bench/dev.json`. |
| `jendhub dev build` | Builds `data/dev` from `bench/dev.json`. |
| `jendhub enrich` | Writes search metadata for each definition of the dev corpus. |
| `jendhub uses ID` | Shows the definitions that a definition uses, and the ones that use it. |
| `jendhub bench ...` | Manages and runs the benchmark. See [bench/README.md](bench/README.md). |

### Sync

The sync walks the BendHub list from the newest package. It stops at the first
package that it knows. It stores the new packages from the oldest, in batches
of 8. Thus an interrupted sync continues with no gap. The sync rejects a
release with an invalid name or version, or a package with an invalid file
name. It remembers each rejection and never tries that package again. A hash
mismatch, a network error or a listing without a valid hash stops the sync.

### Dev corpus

The dev corpus is a small corpus for the benchmark. `bench/dev.json` freezes
it: the Base tag, and the hash, name and version of each package. It holds the
hottest release of each named package, and skips unnamed and rejected packages.
A package that does not fit the definition budget is skipped, together with the
other releases of the same name. Today it holds 37 packages with 5,473
definitions.

### Uses

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

The `jendhub-index` program in `indexer/` loads each file of the dev corpus as
the compiler loads the file it checks, with the packages of `data/full`. It
writes the uses as JSON. Python maps them to definition IDs and drops the
names that are not definitions, for example a `def` that fills a law of an
imported module.

The graph holds only the uses inside one release. A use of Base or of another
package is ignored, and Base is a package of its own. In the dev corpus, 14,581
uses connect the definitions, and 4,625 of 5,473 definitions have at least one
user. One file does not load with today's Base: `bend-collections-laws` declares
a constructor `Ready`, and Base declares one too. The compiler refuses it, so
it has no uses.

## Data layout

The data directory uses the `BEND_LIB` layout of Bend. Git ignores it.

| Path | Content |
|---|---|
| `full/lib/0x<hash>/<path>` | The files of a package. |
| `full/lib/names/<name>@<version>` | The hash of a named release. |
| `full/base/base.bend`, `full/base/tag` | The latest released `base.bend` and its tag. |
| `full/rejected/<hash>` | The reason for a rejection. |
| `full/staging/` | Packages during an atomic write. |
| `dev/` | The dev corpus, in the same layout. |
| `embeddings.sqlite` | The embedding cache, keyed by provider, model and text hash. |
| `enrichments.sqlite` | The enrichment store and the rejected windows, keyed by the hash of each request. |
| `dev-corpus.txt` | The dev corpus as one text file, for query writers. |

## Architecture

| Module | Job |
|---|---|
| `hub.py`, `base.py`, `sync.py`, `mirror.py` | Read BendHub and GitHub, and keep the mirror. |
| `dev.py` | Select and build the dev corpus. |
| `definitions.py` | Extract definitions from source files. One extractor decides what a definition is. |
| `tokens.py`, `bm25.py`, `names.py` | Lexical search: the tokenizer, BM25 and name overlap. |
| `embeddings.py`, `dense.py` | Dense search with cached embeddings. |
| `enrich.py`, `enrichments.py`, `enrich.md` | The enricher, its store and its prompt. |
| `roles.py` | The role feature from the enrichments. |
| `uses.py`, `indexer/` | The use graph and the Rust program that resolves names for it. |
| `fusion.py`, `rerank.py` | Combine pipelines: rank fusion and a linear reranker. |
| `pipelines.py` | The registry of named pipelines. |
| `evaluate.py`, `bench.py`, `judgments.py`, `judge.py`, `pooling.py` | The benchmark. |
| `openrouter.py`, `http.py` | The HTTP client, OpenRouter requests, retries and usage. |

### Pipelines

A `Pipeline` takes a batch of queries and returns one ranked list of IDs for
each query. A `Scorer` gives a score to each candidate ID of each query. Both
take batches, so dense search embeds all queries in one call.

- Retrievers index `(id, text)` pairs. The builder chooses the text view of
  each definition: the code, the head and the package name, the name, or the
  code with the enrichment.
- `Bm25`, `NameMatch` and `Dense` are pipelines and scorers.
- `ReciprocalRankFusion` merges the lists of any pipelines.
- `LinearRerank` takes the top 50 results of one pipeline and sorts them by a
  weighted sum of scorers. `MaxNormalized` scales a scorer to 0..1 for each
  query.
- `PIPELINES` in `pipelines.py` maps each name to a builder. A builder takes
  `Resources`: the definitions, the embedder and the enrichments.

BM25 is our own implementation with `k1 = 1.2` and `b = 0.75`. SQLite FTS5 and
Tantivy fix these values, and they do not let us control the tokenizer. The
tokenizer keeps each whole identifier and also gives its parts. For example,
`get.u32le` gives `get.u32le`, `get`, `u32le`, `u32` and `le`.

### The current best pipeline

`dense-enriched+text+bm25-enriched+role+name-rerank` takes the top 50 results
of dense search on the enriched text. It sorts them by this weighted sum:

| Weight | Feature |
|---|---|
| 1.0 | Cosine similarity on the enriched text: the code, the summary and the queries |
| 0.3 | Cosine similarity on the code |
| 0.075 | BM25 on the enriched text, max-normalized |
| 0.03 | 1 for an API definition, 0 for a helper |
| 0.05 | Name overlap |

Its nDCG@10 on the dev benchmark is 0.815, and 0.830 on the keywords, phrase
and description queries. See [bench/README.md](bench/README.md) for all results
and for the weight tuning.

## External models

| Job | Model | Provider | Notes |
|---|---|---|---|
| Embeddings | `qwen/qwen3-embedding-8b`, 4096 dimensions | Nebius, pinned | All cached vectors must come from one provider. |
| Enrichment | `deepseek/deepseek-v4-flash-0731`, no reasoning | `deepinfra/fp8`, then `parasail/fp8` and `digitalocean` | |
| Benchmark judge | `openai/gpt-6-luna`, medium reasoning | OpenAI, pinned | |

Queries get the Qwen3 instruction format: `Instruct: <task>\nQuery:<query>`.
Documents get no instruction.

### Enrichment

The enricher writes a summary, 3 to 5 search queries and a role (`api` or
`helper`) for each definition. Each request holds a window of one source file:
40 definitions before the targets, 40 targets and 20 definitions after them.
The window keeps the source lines between definitions, so the model sees
comments, the fillings of laws and the places that use each target. This helps
it to decide the role, and to see when a `law` is only a forward declaration.
A limit of 30,000 characters on each side keeps very large definitions out of
the context. The input to output token ratio is about 2 to 1. The first dev
corpus of 20 packages cost about $0.13. The extension to 37 packages cost about
$0.28.

Each window gets one request. If the answer is invalid, the store remembers the
window as rejected, and `jendhub enrich` never sends it again. A window can fail
this way when it holds many similar names: the model then repeats or skips some
names. A failed request is not remembered: `jendhub enrich` stops with an error,
and the next run sends the window again. `jendhub enrich` sends only the
windows that have no stored result and no rejection.

A definition without an enrichment uses only its code in each enriched text
view, and gets 0 from the role feature. In the dev corpus, one window of 40
proof steps is rejected.
