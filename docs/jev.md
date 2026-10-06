# Jev

Jev is a decision model from TypeSafe. It does not generate text. It reads a
`state` and answers typed questions with probabilities. We call Jev through
OpenRouter with the official TypeSafe Python SDK (`typesafe-sdk`).

## Access

- Endpoint: the SDK sends `POST https://openrouter.ai/api/v1/systemone`.
- Key: `OPENROUTER_API_KEY` from `.env`. The dev shell exports it.
- Model: `jev-1.13`. OpenRouter routes it as `typesafe/jev-1.13`. The response
  names the snapshot, for example `typesafe/jev-1.13-20260917`.
- Price: $0.042 per million input tokens. Output tokens are free.
- Speed: approximately 0.3 to 0.7 seconds for each request.

Sources:
[API reference](https://docs.typesafe.ai/api),
[models](https://docs.typesafe.ai/models),
[OpenRouter SDK guide](https://openrouter.ai/docs/guides/community/typesafe-sdk),
[jev-1.13 failure modes](https://docs.typesafe.ai/model-jaggedness/jev-1.13).

## Request

A request has a `state` and a map of `questions`. The `state` is a string, a
JSON object or a JSON array. Instructions can refer to a field of the state by
its name in backticks, for example `query`.

There are three question types:

| Type | Asks | Answer |
| - | - | - |
| `choice` | Which option from a set of keys? | `choice`, `probabilities` for each key (sum is 1), `confidence` |
| `score` | Which level on an ordered scale? | `score`, `probabilities` for each level, `confidence` |
| `noul` | Is a statement true? | `noul`, the probability of yes |

The model does not see the question id. The model sees the option keys and
their descriptions. A description can be `null` when the state already holds
the text for the key.

## Limits

- A `choice` accepts a maximum of 255 options.
- The `state` plus the longest question must be less than 32k tokens.
- The `state` plus all questions must be less than 64k tokens.
- Accuracy decreases when the state holds much text that is not related to
  the question.

## Design

The search uses the same plan as web search engines: much work when a
definition enters the index, little work for each query. Jev reads only the
final candidates.

The index holds Base, the standard library from the `main` branch of
`bendlang/bend` (`bend2/base.bend`), and the latest version of the 50 hottest
packages (`corpus.HOTTEST_PACKAGES`). Base ranks before all packages; the
packages rank by `hot`. Definitions with the same signature and doc comment are
one document; the document shows the highest-ranked package that has it.

When a definition enters the index (`jend.index`):

1. `deepseek/deepseek-v4-flash` writes a one-sentence summary and 3 likely
   queries for the definition, from its signature, doc comment, file and
   package description (`jend.enrich`). One request holds up to 24
   definitions of one file.
2. `qwen/qwen3-embedding-8b` makes a 1024-dimension vector of the document
   text (`jend.embed`).
3. A BM25 keyword index is built from the same text when the index loads. Names
   are split into words.

For each query (`jend.search`):

1. The code returns the cached ranking if one exists.
2. BM25 and vector search each rank the documents. Reciprocal rank fusion
   merges the best 100 of each list. The package rank breaks ties. 50
   candidates continue.
3. One Jev request asks one `noul` for each candidate: "Would a programmer call
   definition `definitions.d07` directly to do what `query` asks for?". The
   criteria tell Jev that an internal helper that performs one step for another
   definition is not a match. The state holds the query and a card for each
   candidate: name, package, file, doc comment, signature (up to 600
   characters) and summary.
4. The engine returns all 50 candidates in descending order of their `noul`.
   There is no threshold: the user decides. The cache key contains the index id
   and a hash of the source of the ranking modules (`search.RANKING_MODULES`).
   A change to the ranking code starts a new cache, so the server does not
   return rankings of an earlier algorithm.

The web server (`jend.web`) serves the same ranking as an HTML page and as
JSON. Before it pays for a new query, it checks a rate limit for each client
and a daily budget (`jend.limits`). It computes the cost of a query from the
input tokens of the Jev request ($0.042 per million tokens; the SDK drops the
`cost` field of OpenRouter) and the cost that OpenRouter reports for the
embedding.

Latency of a new query: Jev answers in approximately 0.5 s. The embedding
request through OpenRouter is usually 0.2 to 0.6 s, but it sometimes stalls for
several seconds; one request took 58 s with two attempts. A second request at
the same moment stalled too, so the delay is upstream.

## Results on the real index

Index: Base (463 definitions) and the latest version of the 50 hottest
packages, 53,511 definitions, 43,952 documents. All documents have a summary.
The enrichment model sometimes returns the list of items without the `items`
object; `jend.enrich` accepts both forms.

Cost of the index, one time: enrichment approximately $0.95, embeddings
$0.06.

Cost of a query that is not in the cache: one Jev request of approximately 10k
input tokens ($0.00042) and one query embedding ($0.0000003). Time:
approximately 0.5 s for the embedding and 0.4 s for Jev. One measured query took
52 s; a second measurement did not show this delay.

[benchmark.md](benchmark.md) gives the benchmark and the measurements of the
current design.

### Internal helpers

This experiment used an earlier set of 69 labeled queries with one correct
answer each. The benchmark replaced that set.

Helpers such as `utf8.decode.go` or `pct.enc.path` got high `noul` values
because they do the work that the query names. We compared four orders. The
helper share is the share of the top 5 results whose name has a helper form
(`.go`, `.if`, `.step`, `internal_` and similar), on the labeled queries and on
14 short queries such as `url encode` and `sort`.

| Order | Top 1 | Mean reciprocal rank | Labeled definitions in top 5 | Helper share, labeled | Helper share, short |
| - | - | - | - | - | - |
| "Does it do what `query` asks for?" | 67/69 | 0.980 | 0.947 | 0.099 | 0.071 |
| Same, documented definitions + 0.1 | 66/69 | 0.973 | 0.952 | 0.113 | 0.100 |
| "Would a programmer call it directly?" | 68/69 | 0.993 | 0.985 | 0.064 | 0.029 |
| Same, documented definitions + 0.1 | 67/69 | 0.983 | 0.982 | 0.075 | 0.029 |

The question about a direct call is better on each measure, so the engine
uses it. A bonus for a doc comment makes the order worse, because many helpers
have doc comments and many public definitions do not.

With the direct-call question, keyword search has a correct document in its
best 50 for 66/69 queries, vector search for 69/69, and the merged candidates
for 69/69.

## Experiments

These experiments measured an earlier design with two `choice` requests:
request 1 ranked packages, and request 2 ranked the definitions of the best
packages. The design above replaces it.

Data: 20 synthetic packages with 178 signatures (147 `def`, 23 `type`, 8 `law`)
and 61 labeled queries. 6 of these queries have no match.

Variants that we compared, each query 2 times:

| Request | Variant | Top 1 | Input tokens |
| - | - | - | - |
| Packages | catalog in `criteria`, query in `state` | 55/55 | 1464 |
| Packages | catalog and query in `state`, `criteria` null | 54/55 | 1017 |
| Packages | catalog in `state`, query in `instructions` | 54/55 | 1008 |
| Definitions, 3 packages | signatures in `state`, `criteria` null | 55/55 | 1810 |
| Definitions, 3 packages | signatures in `criteria` | 55/55 | 2294 |
| Definitions, 3 packages | signatures with context in `criteria` | 55/55 | 3631 |
| Definitions, all 20 packages | signatures in `state`, `criteria` null | 55/55 | 9735 |

Results of the full two-request search with the chosen structure:

| Shortlist | Top 1 | Mean packages | `exists` with a match | `exists` without a match |
| - | - | - | - | - |
| mass 0.9, maximum 3 | 55/55 | 1.15 | min 0.18, mean 0.86 | max 0.08 |
| maximum 1 | 54/55 | 1.00 | min 0.05, mean 0.85 | max 0.08 |

## Findings

1. All variants find the correct definition on this data. The synthetic set is
   too easy to separate the variants by accuracy. We selected the variant with
   the fewest tokens: the text goes in the `state` one time, and the criteria
   are `null`.
2. A `choice` distribution is sharp. The first package usually has 0.9 or more.
   For a query without a match, the first package also gets 0.7 to 0.99.
3. Request 1 can put the correct package second. Example: "percent-encode a
   string for a URL query" gives `text` 0.71 and `http-client` 0.15. A shortlist
   of one package loses the match. The 0.9 mass shortlist keeps it, and
   request 2 then selects `Url.encode_component` with 0.98.
4. A `noul` about packages in request 1 does not separate queries well. Package
   descriptions do not list all functions, so the `noul` is low for some
   correct queries (0.18). We removed it from request 1.
5. The `noul` in request 2 separates queries with a match from queries without
   a match: all queries without a match are at 0.08 or less. Some correct
   queries are low because Jev reads literally. Example: "check whether a
   string looks like an email address" gets 0.16 for `Regex.matches`.
6. One request held 178 definitions (9735 tokens) without loss of accuracy. The
   limit of 255 options for each `choice` and the 32k token limit will control
   how many packages and definitions fit in one request on the real hub.
7. Two runs of the same request give probabilities that differ by
   approximately 0.01 to 0.05.

## Scale

BendHub has more than 500 packages now. We expect thousands of packages and
hundreds of thousands of definitions.

### Budget

- Rate limit for each account: 100k tokens and 40 requests each second.
- A package card (name, description and a `noul` question) is approximately
  130 tokens. With the definition names of a small package it is approximately
  190 tokens.
- A full scan of 5000 package cards is approximately 1M tokens: $0.04 and
  10 seconds of the rate limit for each query. A full scan of 100k signatures
  is approximately 5M tokens. A full scan is not possible for each query.

### How omp `find` and jegrep use Jev

omp `find` (`packages/coding-agent/src/tools/jfind/` in
[oh-my-pi](https://github.com/can1357/oh-my-pi)) copies the `cascade` strategy
of [jegrep](https://github.com/can1357/jegrep):

1. A local lexical scan ranks all files. Keywords come from the query and from
   the caller. Rare keywords get a higher IDF weight. The top 128 files continue.
2. Jev judges the 128 file names: one `noul` for each file, 64 files in each
   request.
3. The code reads 20 files, cuts them into 8 KB windows and sends a 384-byte
   sketch of each window: 46 sketches in each request, one `noul` for each.
4. Jev verifies the complete text of the best 40 windows (sketch `noul` 0.45
   or more). Only these answers make results (`noul` 0.2 or more).

16 requests run at the same time. A search costs approximately $0.005 and
takes approximately 2 seconds. Important decisions:

- One `noul` for each entry, not one `choice` for all entries. A `noul` is an
  absolute probability, so answers from different requests are comparable.
- Cheap evidence first (names, sketches). Expensive evidence (full text) only
  for the best candidates.
- jegrep measured other strategies. A `choice` beam search over the directory
  tree lost to the cascade. A local-only shortlist ("Sieve") lost recall on
  unseen queries (80%).

TypeSafe cookbooks show the same patterns:
[re-ranking](https://docs.typesafe.ai/cookbooks/rerank_typesafe) (BM25
shortlist, then one `noul` for each candidate) and
[hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification)
(a `choice` at each level of a taxonomy, with a beam of the best 3 paths).

### Measurements on the synthetic data

Package ranking, 55 queries with a match and 6 queries without a match:

| Variant | Top 1 | Top 3 | Best score for a query without a match |
| - | - | - | - |
| `choice`, one request | 54/55 | 55/55 | 0.98 |
| `choice`, 4 shards of 5 packages | 47/55 | 55/55 | 1.00 |
| `noul` for each package, one request | 54/55 | 55/55 | 0.24 |
| `noul` for each package, 4 shards | 53/55 | 55/55 | 0.36 |
| `noul` for each package with definition names, 4 shards | 55/55 | 55/55 | 0.12 |
| BM25 on descriptions, no Jev | 28/55 | 35/55 | - |
| BM25 on descriptions and signatures, no Jev | 37/55 | 44/55 | - |

- A `choice` cannot be split into shards: each shard gives its best package
  approximately 1.0.
- A `noul` can be split into shards without loss.
- Definition names in the package card make the `noul` stronger: the 10th
  percentile of the correct package goes from 0.61 to 0.89.
- A `noul` shows when no package matches. A `choice` does not.
- BM25 alone misses many correct packages. The queries describe behavior, and
  the packages use other words.

## Open questions

- Each Jev card holds up to 600 characters of signature. Shorter cards can
  decrease the cost of each query. The labeled queries must show that accuracy
  does not decrease.
- The labeled set has only 6 queries without a match. The threshold needs more
  of them.
