# The JendHub benchmark

The benchmark measures each search pipeline on the dev corpus with nDCG@k.
The production pipeline runs through the real search path: the worker code
indexes the dev corpus into a generation of its own in Postgres and Qdrant, and
the search engine answers each query. The other pipelines are experiments of
the `lab` package. They run in memory on the definitions of the same
generation.

The commands need the dev services. Start them in one terminal, and run the
commands in another:

```bash
nix develop -c process-compose up
nix develop
jendhub bench check
```

| Command | What it does |
|---|---|
| `jendhub bench corpus` | Writes the dev corpus as one text file, `data/dev-corpus.txt`, for query writers. |
| `jendhub bench check` | Checks `queries.jsonl`, and prints a summary with the number of current and stale judgments. |
| `jendhub bench run <pipeline> [--k N ...]` | Prints nDCG@k and judged@k for all queries and for each kind. |
| `jendhub bench compare <A> <B> [--k N]` | Compares two pipelines query by query. |
| `jendhub bench pool --pipelines <name> ... [--depth N]` | Judges the top results of the named pipelines. |
| `jendhub bench agreement` | Judges the original answers and random definitions, and prints the agreement. |
| `jendhub bench judge [--dry-run]` | Judges each pair with a stale grade again. `--dry-run` prints the number of pairs and the size of the messages, and sends nothing. |

## Files

| File | Content |
|---|---|
| `dev.json` | The frozen dev corpus: the Base tag and 44 packages. |
| `PROMPT.md` | The prompt that made queries 201 to 300. |
| `queries.jsonl` | 500 queries with graded answers. |
| `JUDGE.md` | The prompt of the LLM judge. |

## Queries

Each line is `{"query", "kind", "answers": [{"id", "grade"}]}`. The queries
imitate an AI agent that searches before it knows the name of the definition.
Thus most queries are short and vague. The targets are API definitions, not
helpers, tests or examples.

Three runs wrote the queries.

1. The first run wrote 200 queries of 8 kinds for the corpus of 20 packages.
   One agent read the whole dev corpus and wrote the queries with their
   answers. It used an earlier version of `PROMPT.md`.
2. The second run wrote 100 keywords, phrase and description queries for the
   corpus of 37 packages, in the same way. It used the current `PROMPT.md`.
   Agents will mostly search with a few words, like a person who searches with
   Google. These three kinds are the main goal of JendHub.
3. The third run wrote 200 keywords, phrase and description queries for common
   library tasks, after the corpus grew to 44 packages (see below). The agent
   wrote each query first. Then it found the answers in the source: it read
   the API of each package that can answer, searched the definitions by name
   and text, and read the top 10 results of the production search. A query
   went in when the corpus holds a grade-3 answer, also when the search misses
   it. Thus the run does not prefer queries that the search already answers
   well.

### The extension of the corpus

The first 36 packages hold few general library definitions. On the full
corpus, searches for common tasks showed three kinds of difficulty that the
dev corpus did not have:

- Proof packages fill the results for basic tasks. For "list filter", the
  laws of `bend-mathlib` about `filter` take ranks 2 to 8.
- Several packages answer the same task: four JSON libraries, two time
  libraries, two container libraries.
- Some packages copy code of others. The `spec` and `proofs` modules of
  `bend-collections` repeat its `src` functions.

The third run added 8 packages, chosen for the most useful queries and
distractors for each added definition:

| Package | Definitions | Why |
|---|---|---|
| `bend-collections` | 4,483 | Containers, cryptography and math, with spec and proof copies of its own code |
| `bend-mathlib` | 952 | Laws about lists, strings and naturals, which compete with Base for basic tasks |
| `bend-time-lib` | 423 | Dates, durations and ISO 8601, beside `bend-kit-time` |
| `emerging-ezjson` | 332 | A second JSON library, beside `bend-kit-json` |
| `bend-kit-regex` | 316 | Regular expressions |
| `bend-kit-bignum` | 136 | Big integers, decimals and rationals |
| `bend-kit-fmt` | 59 | String formatting and float printing |
| `bend-kit-csv` | 34 | CSV |

The dev corpus now has 12,208 definitions. Packages that copy whole other
packages (`bend-collections-laws-crypto`, `bend-collections-laws-math`, 14,407
definitions) and generated tables (`bend-kit-unicode`, 13,301 definitions)
stayed out, because they would more than double the corpus.

When the corpus grew, 7 packages got a new version. The answers of the first
run now point to the same names in the new versions. In 4 of these answers the
code changed, but the purpose did not, so their grades stayed. The judgments
of the 26 changed definitions were deleted and judged again by pooling.

| Kind | Queries |
|---|---|
| keywords | 146 |
| phrase | 130 |
| description | 105 |
| question | 35 |
| signature | 30 |
| name | 24 |
| exact-name | 15 |
| exact-signature | 15 |

Grades: `3` is exactly what the query asks for, `2` is a good answer, `1` is
related and useful. Each query has at least one grade-3 answer. The queries
hold 2,172 answers: 761 with grade 3, 727 with grade 2 and 684 with grade 1.
The third run gave grade 1 to a spec copy of the function a query asks for,
and no grade to proofs, laws and internal steps.

## Metric

nDCG@k uses the gain `2^grade - 1` and the discount `log2(rank + 1)`, where the
first result has rank 1. The
ideal ranking comes from all known grades of the query. A result with no grade
counts as grade 0. `--k` sets the cutoff. The default is 10.

judged@k is the share of the top k results that have a grade. A low judged@k
makes nDCG@k too low, because unknown relevant results count as wrong.

## Judgments and pooling

The query writer graded only the answers that it found. A strong pipeline
finds other relevant definitions. Pooling fixes this: an LLM judge grades the
top results of chosen pipelines.

- The judge is `openai/gpt-6-luna` with medium reasoning, on OpenAI. It grades
  one query and one definition in each request, on the same 0 to 3 scale. A
  JSON schema restricts the answer to the grade.
- The judge does not see the query kind. From the use graph, it sees the
  definitions that the definition uses and the definitions that use it, at most
  10 heads each. It also sees the roots that reach the definition: the
  definitions that reach it through a chain of uses but that no definition of
  its package uses, at most 5 heads. The uses help the judge to see what the
  definition does. The users and the roots help it to see if the definition is
  a helper.
- The `judgments` table in Postgres stores each grade with its query, its definition ID
  and the SHA-256 of the full request. Grade 0 rows are kept. No row is ever
  deleted.
- A grade is current if its request is the request that the judge gets today
  for the same pair. A change of `JUDGE.md`, of the message format, of the
  definition text or of its uses makes the grade stale. `bench run` and
  `bench compare` refuse to run while a grade is stale. `bench judge` judges
  the stale pairs again. If a request goes back to an earlier form, its old
  grade is current again.
- In the evaluation, an original answer always wins over a judgment.
- `bench pool` skips pairs that already have a current grade. Thus a later run
  judges only the new results.
- Pool only good pipelines. A weak experimental pipeline adds noise and cost.

**The pooling rule:** pool a new pipeline before you compare it. Trust a
comparison only when judged@10 is about 0.95 or more for both pipelines. An
unpooled pipeline looks worse than it is. For example, before pooling,
`dense-head-package` was 0.075 below `dense-text`, mostly from unjudged results.

With `--top N`, the benchmark skips each query with an answer outside the N
hottest packages, and the judgments outside these packages. Results move up
from below rank 10, so judged@10 can drop. Pool with the same `--top N` before
you compare. These judgments are valid for the whole dev corpus too.

To add a pipeline:

```bash
jendhub bench pool --pipelines <new-pipeline>
jendhub bench compare <baseline> <new-pipeline>
```

These pipelines are pooled at depth 10:

- `bm25-head-package+name-rerank`
- `dense-text+name-rerank`
- `dense-text-summary+name-rerank`
- `dense-enriched+name-rerank`
- `dense-text+metadata+name-rerank`
- `dense-enriched+text+bm25-enriched+role+name-rerank`
- `dense-origin+graph+signature+name-rerank`

Pooling costs about $0.15 for 1,000 pairs.

### Judge validation

`bench agreement` judges the original answers blind, and one random
definition for each query. The random definitions come from a fixed seed. The
shares of each judged grade on all 300 queries:

| Original grade | Pairs | Judged 0 | Judged 1 | Judged 2 | Judged 3 |
|---|---|---|---|---|---|
| 3 | 478 | 0.00 | 0.14 | 0.10 | 0.77 |
| 2 | 525 | 0.01 | 0.64 | 0.21 | 0.14 |
| 1 | 424 | 0.03 | 0.81 | 0.11 | 0.04 |
| random | 300 | 0.96 | 0.04 | 0.00 | 0.00 |

The judge gives the same grade in 57% of the original answers, and calls 99%
of them relevant. It is stricter than the query writer on grades 2 and 3.

## Comparisons

`bench compare` computes nDCG@k of each query for both pipelines. For all
queries and for each kind, it prints the mean change, a 95% bootstrap interval
for the change (10,000 resamples, seed 0), and the number of queries that got
better and worse. A paired comparison finds much smaller differences than two
separate scores. The noise of one score is about ±0.04 for all queries and
about ±0.11 for a kind with 30 queries.

## Results

This table and the sections after it, up to "The extended benchmark", measure
the first 300 queries on the corpus of 36 packages. nDCG@10 and judged@10 use
the judgments of that time. The short column is the mean of the 181 keywords,
phrase and description queries, weighted by the number of queries of each
kind.

| Pipeline | nDCG@10 | short | judged@10 |
|---|---|---|---|
| `bm25` | 0.380 | 0.334 | 0.639 |
| `bm25-head-package` | 0.424 | 0.362 | 0.850 |
| `bm25-head-package+name-rrf` | 0.428 | 0.365 | 0.853 |
| `bm25-head-package+name-rerank` | 0.460 | 0.396 | 1.000 |
| `dense-head-package` | 0.677 | 0.684 | 0.842 |
| `dense-text` | 0.706 | 0.705 | 0.984 |
| `dense-text+name-rerank` | 0.716 | 0.713 | 1.000 |
| `dense-text+metadata+name-rerank` | 0.757 | 0.772 | 1.000 |
| `dense-text-summary+name-rerank` | 0.772 | 0.786 | 1.000 |
| `dense-enriched+name-rerank` | 0.773 | 0.797 | 1.000 |
| **`dense-enriched+text+bm25-enriched+role+name-rerank`** | **0.820** | **0.833** | **1.000** |
| `dense-origin+graph+signature+name-rerank` | 0.816 | 0.811 | **1.000** |

Rows with judged@10 below 0.95 are not pooled, so their scores are too low.
The production row comes from the search engine with the production vector
setting (see below). Pooling judged its 79 new pairs. Before the move to the
engine, the same ranking scored 0.819. Against the best enriched pipeline, the
difference is -0.004, interval [-0.020, +0.012].

Main findings:

- Dense search beats BM25 by a large margin on natural-language queries:
  `dense-text+name-rerank` against `bm25-head-package+name-rerank` gains
  +0.248 on keywords, +0.308 on phrase and +0.403 on description. On
  exact-signature queries, it gains only +0.055.
- Rank fusion of dense and lexical results made results worse: equal weight
  for a weak list hurts. A linear rerank with small weights works.
- Enrichment improves natural-language queries. `dense-enriched+name-rerank`
  against `dense-text+name-rerank` gains +0.070 on keywords, +0.094 on phrase,
  +0.088 on description and +0.082 on name. Alone it hurts exact-signature
  queries by 0.083.
- The best pipeline adds the code embedding, BM25 and the role to the rerank.
  Against `dense-enriched+name-rerank`, it gains +0.047 on all queries,
  +0.117 on signature, +0.076 on exact-signature, +0.044 on keywords, +0.037
  on phrase and +0.025 on description. Each gain except exact-name is
  significant.
- The role feature matters more since the judge sees the use graph. The best
  weight set without it scores 0.033 lower on all queries.
- Without enrichment, the use graph, the doc comment and a structural
  signature match recover most of the gain of enrichment.
  `dense-origin+graph+signature+name-rerank` against `dense-text+name-rerank`
  gains +0.103 on all queries, +0.099 on keywords, +0.116 on phrase, +0.095 on
  description and +0.201 on signature. Against the best enriched pipeline, it
  is equal on all queries (-0.001, interval [-0.017, +0.015]). It is better on
  signature (+0.148) and worse on question (-0.056). On the short queries, it
  is 0.016 below.
- Description is the weakest of the short kinds (0.769 with enrichment, 0.751
  without).

## Weight tuning

The weights of the best pipeline were tuned on all 300 queries for nDCG@10. A
grid search tried 4,200 weight sets for the code, BM25, role and name
features. The weight of the enriched embedding stays 1.0, because only the
ratios change the order.

| Feature | Grid | Best | Flat range |
|---|---|---|---|
| Code embedding | 0 to 1.0 | 0.4 | 0.2 to 0.5 |
| BM25, max-normalized | 0 to 0.2 | 0.075 | 0.05 to 0.1 |
| Role | 0 to 0.5 | 0.075 | 0.075 to 0.125 |
| Name overlap | 0 to 0.3 | 0.05 | 0 to 0.15 |

The best weights gave +0.010 against the weights before the new judge (0.3,
0.075, 0.03, 0.05) on the same queries. Of the top weight sets, they have the
highest judged@10, 0.982 before pooling. The maximum of many weight sets on
the same queries is too high. A 5-fold cross-validation selected the weights
on 4 folds and measured them on the fifth. Over 20 random splits, the gain on
the unseen queries was +0.005 on average, and it was positive in each split.

### The pipeline without enrichment

`dense-origin+graph+signature+name-rerank` was built without new judgments
until the last step. The top results of a new pipeline hold unjudged
definitions, so each step measured two scores: nDCG@10 with unjudged results
as grade 0, which is too low, and condensed nDCG@10, which drops the unjudged
results and is too high. The true score is between them.

1. The query instruction changes the score of the same documents by about
   0.04. Of 11 instructions, the one that names a programming task and a
   function to call was best.
2. Coordinate ascent tuned the weights of 24 features for the mean of the two
   scores. A backward elimination removed the features with no effect, and a
   5-fold cross-validation chose between the sets that remained.
3. A second embedding of each definition, with the names of its users, gave
   about +0.007. It was dropped, because it doubles the size of the index.
4. One embedding and 8 other features gave 0.804 and 0.819 on unseen
   queries. Pooling then judged the new pairs in the top 10. With judged@10 at
   1.000, the pipeline scores 0.819, at the top of this interval.

These features had no effect when the chosen ones were present: an embedding of
the code alone, of the head and the package, or of the code with the heads of
its uses; BM25; a second query instruction; pseudo-relevance feedback; the
number of other releases that use a definition; users in other modules or in
laws; and the kind of the definition. A linear model that learns only from
judged candidates (LambdaRank) gave a higher condensed score and a lower
score with unjudged results as 0. Without judgments, the two cannot be
compared, so the simpler coordinate ascent stays.

### The production search at scale

The search engine keeps the features and weights of
`dense-origin+graph+signature+name-rerank`, but it reads them from the index
instead of from memory. Each change for scale was measured against the exact
search with full vectors on the 300 queries:

| Change | nDCG@10 change | Queries worse / better |
|---|---|---|
| HNSW instead of exact search | ±0 | 0 / 0 |
| `ReachedMatch` from a pool of the top 1,000 instead of all definitions | ±0 | 2 / 1 |
| int8 or binary quantization, rescored with full vectors | ±0 | 0 / 0 |
| 2,048 dimensions instead of 4,096 | -0.006 [-0.009, -0.003] | 132 / 73 |
| 1,024 dimensions | -0.011 [-0.016, -0.006] | 166 / 81 |
| 512 dimensions | -0.022 [-0.028, -0.016] | 187 / 70 |
| Exact-name candidates beside the ANN candidates | ±0 | 0 / 0 |
| Trigram name candidates | -0.0004 | 5 / 0 |

The quality bar allows a loss of 0.01. The cheapest setting inside it is 2,048
dimensions with binary quantization: 256 bytes of RAM for each definition, and
8 KB on disk for the rescoring vectors. The losses above count each new
unjudged result as grade 0, so they are upper bounds. The embedding cache keeps
the full vectors, so a change of the setting needs no new embeddings.

A use of an older release of a package now counts as a use of the same module
and name in its latest release. This marks 64 more definitions of the dev
corpus as used by another release, for example the functions of
`bend-kit-bytes` that other packages import through an older version. It
moved nDCG@10 by -0.0006 before pooling.

With all of these changes and the new pairs judged, the engine scores 0.816,
0.003 below the exact search in memory.

The dev corpus is too small to show the recall of the HNSW index. On the
first live index, 56,650 definitions, the 300 queries compare the production
search with exact search on the same 2,048-dimension vectors: recall is 1.000
at 10 and at 50, and 0.999 at 1,000, the pool of `ReachedMatch`. Against exact
search on the full 4,096 dimensions, recall is 0.92 at 10 and 0.90 at 1,000,
which is the cost of the cut that the dev benchmark measures. After the rerank,
the production engine and an engine with exact full-dimension search agree on
the first result for 95% of the queries, and share 94% of the top 10. The
production vector search takes 16 ms (median) and 17 ms (p95) for a pool of
1,000.

The Python extraction and the compiler declare the same names in each of the
2,026 files of the latest releases that load. The worker checks this for each
file and logs each difference.

`bend-collections-laws@1.0.0.0` left the dev corpus, because it does not load
with Base v2.0.37 and is therefore invalid. Its only file imports other
packages and declares no definition, so the definitions, queries and scores
did not change.

## The extended benchmark

The 500 queries on the corpus of 44 packages are not pooled yet. The new
packages put unjudged definitions into the top 10 of the old queries too. The
production pipeline gives these scores. nDCG@10 counts each unjudged result
as grade 0, so it is too low. The condensed score drops the unjudged results
from the ranking, so it is too high. The true score lies between them.

| Queries | nDCG@10 | condensed | judged@10 |
|---|---|---|---|
| all 500 | 0.769 | 0.852 | 0.613 |
| the first 300 | 0.738 | 0.809 | 0.824 |
| the 200 new ones | 0.814 | 0.917 | 0.296 |
| new keywords (86) | 0.848 | 0.938 | 0.310 |
| new phrase (63) | 0.834 | 0.922 | 0.276 |
| new description (51) | 0.731 | 0.873 | 0.296 |

On the corpus of 36 packages, the first 300 queries scored 0.816 with each
result judged. Their condensed score on the larger corpus, 0.809, shows that
the new distractors push some graded answers out of the top 10.

The top 10 hold 1,937 pairs without a grade. Pooling them costs about $0.30.
Pool before you compare pipelines on the extended benchmark.

The new packages have no enrichments, so the enriched lab pipelines see only
part of the corpus enriched until `jendhub enrich` runs. The production
pipeline needs no enrichment.

The production search finds no grade-3 answer in the top 10 for five new
queries. The answer is in the corpus each time:

| Query | Grade-3 answer |
|---|---|
| Remember which items I have already seen so that I do not process them twice. | `Set`, `Set.add`, `Set.has` |
| Undo the last action by keeping a history of actions in a last-in first-out structure. | `stack:Stack` of `bend-collections` |
| I need a map that keeps its keys sorted so I can get all entries between two keys. | `balanced_search_tree:sub_map` |
| Pull the year, month and day out of a date string with one pattern. | `regex:find` |
| Prove that a message came from me with a public key signature. | `sign:sign` (rank 9) |

## Limits

- There is no held-out query set. The weights of the rerankers were tuned on
  the first 300 queries, so their gains are a little too high. The best
  weights form a flat plateau, and cross-validation shows a smaller but real
  gain, which limits this risk. The 200 new queries played no part in the
  tuning.
- An LLM wrote the queries, and an LLM writes the enrichment queries. They can
  be more similar to each other than real agent queries are. The third run
  read the production search results while it looked for answers. It added
  every query that the corpus answers, but it graded the definitions that it
  found, and the search found many of them.
- The dev corpus has 12,208 definitions. The search corpus is much larger, with
  more distractors: the live index holds 57,169 definitions of 113 releases.
  The dev corpus cannot show the recall of the HNSW index or of binary
  quantization at that size.
