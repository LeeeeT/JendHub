# The JendHub benchmark

The benchmark measures each search pipeline on the dev corpus with nDCG@k.
All commands run on `data/dev`.

```bash
nix develop
jendhub bench check
```

| Command | What it does |
|---|---|
| `jendhub bench corpus` | Writes the dev corpus as one text file, `data/dev-corpus.txt`, for query writers. |
| `jendhub bench check` | Checks `queries.jsonl` and `judgments.jsonl`, and prints a summary. |
| `jendhub bench run <pipeline> [--k N ...]` | Prints nDCG@k and judged@k for all queries and for each kind. |
| `jendhub bench compare <A> <B> [--k N]` | Compares two pipelines query by query. |
| `jendhub bench pool --pipelines <name> ... [--depth N]` | Judges the top results of the named pipelines. |
| `jendhub bench agreement` | Judges the original answers and random definitions, and prints the agreement. |

## Files

| File | Content |
|---|---|
| `dev.json` | The frozen dev corpus: the Base tag and 20 packages. |
| `PROMPT.md` | The prompt that made the queries. |
| `queries.jsonl` | 200 queries with graded answers. |
| `JUDGE.md` | The prompt of the LLM judge. |
| `judgments.jsonl` | The grades of the LLM judge. |

## Queries

One agent read the whole dev corpus with `PROMPT.md` and wrote 200 queries.
Each line is `{"query", "kind", "answers": [{"id", "grade"}]}`. The queries
imitate an AI agent that searches before it knows the name of the definition.
Thus most queries are short and vague. The targets are API definitions, not
helpers, tests or examples.

| Kind | Queries |
|---|---|
| question | 35 |
| phrase | 32 |
| signature | 30 |
| keywords | 25 |
| name | 24 |
| description | 24 |
| exact-name | 15 |
| exact-signature | 15 |

Grades: `3` is exactly what the query asks for, `2` is a good answer, `1` is
related and useful. Each query has at least one grade-3 answer. The queries
hold 825 answers: 307 with grade 3, 333 with grade 2 and 185 with grade 1.

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
- Each row of `judgments.jsonl` is `{query, id, grade, judge}`. Grade 0 rows
  are kept. The judge name holds the model, the effort and a hash of
  `JUDGE.md`. If the prompt changes, `bench check` reports the old rows. Delete
  them and judge again.
- In the evaluation, an original answer always wins over a judgment.
- `bench pool` skips pairs that already have a grade. Thus a later run judges
  only the new results.
- Pool only good pipelines. A weak experimental pipeline adds noise and cost.

**The pooling rule:** pool a new pipeline before you compare it. Trust a
comparison only when judged@10 is about 0.95 or more for both pipelines. An
unpooled pipeline looks worse than it is. For example, before pooling,
`dense-head-package` was 0.075 below `dense-text`, mostly from unjudged results.

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

Pooling costs about $0.15 for 1,000 pairs.

### Judge validation

`bench agreement` judges the original answers blind, and one random
definition for each query. The random definitions come from a fixed seed. The
shares of each judged grade, with medium reasoning:

| Original grade | Pairs | Judged 0 | Judged 1 | Judged 2 | Judged 3 |
|---|---|---|---|---|---|
| 3 | 307 | 0.00 | 0.02 | 0.07 | 0.91 |
| 2 | 333 | 0.04 | 0.51 | 0.29 | 0.16 |
| 1 | 185 | 0.02 | 0.74 | 0.14 | 0.11 |
| random | 200 | 0.92 | 0.07 | 0.01 | 0.00 |

The judge gives the same grade in 62% of the original answers, and calls 98%
of them relevant. This is in the usual range of agreement between human
assessors. The judge is stricter than the query writer on grade 2. With low
reasoning, the agreement was 59% and 92%.

## Comparisons

`bench compare` computes nDCG@k of each query for both pipelines. For all
queries and for each kind, it prints the mean change, a 95% bootstrap interval
for the change (10,000 resamples, seed 0), and the number of queries that got
better and worse. A paired comparison finds much smaller differences than two
separate scores. The noise of one score is about ±0.04 for all queries and
about ±0.11 for a kind with 30 queries.

## Results

nDCG@10 and judged@10 with the current judgments:

| Pipeline | nDCG@10 | judged@10 |
|---|---|---|
| `bm25` | 0.412 | 0.622 |
| `bm25-head-package` | 0.468 | 0.821 |
| `bm25-head-package+name-rrf` | 0.469 | 0.822 |
| `bm25-head-package+name-rerank` | 0.500 | 1.000 |
| `dense-head-package` | 0.692 | 0.784 |
| `dense-text` | 0.747 | 0.967 |
| `dense-text+name-rerank` | 0.760 | 1.000 |
| `dense-text+metadata+name-rerank` | 0.783 | 1.000 |
| `dense-text-summary+name-rerank` | 0.797 | 1.000 |
| `dense-enriched+name-rerank` | 0.799 | 1.000 |
| **`dense-enriched+text+bm25-enriched+role+name-rerank`** | **0.817** | **1.000** |

Rows with judged@10 below 0.95 are not pooled, so their scores are too low.

Main findings:

- Dense search beats BM25 by a large margin on natural-language queries. On
  signature, exact-name and exact-signature queries, the difference was not
  significant.
- Rank fusion of dense and lexical results made results worse: equal weight
  for a weak list hurts. A linear rerank with small weights works.
- Enrichment improves natural-language queries: name +0.099, phrase +0.078,
  keywords +0.076 for `dense-enriched+name-rerank` against
  `dense-text+name-rerank`. Alone it hurts signature queries.
- The code embedding as a rerank feature repairs the signature queries.
  Against `dense-enriched+name-rerank`, the best pipeline gains +0.065 on
  signature and +0.066 on exact-signature.
- Signature queries remain the weakest kind (0.673). Type-directed search is
  the planned fix.

## Limits

- There is no held-out query set. The weights of the rerankers were tuned on
  these 200 queries, so their gains are a little too high. The best weights of
  the last sweep form a flat plateau, which limits this risk.
- An LLM wrote the queries, and an LLM writes the enrichment queries. They can
  be more similar to each other than real agent queries are.
- The dev corpus has 3,302 definitions. The search corpus will be much larger,
  with more distractors.
