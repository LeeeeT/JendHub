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
| `dev.json` | The frozen dev corpus: the Base tag and 37 packages. |
| `PROMPT.md` | The prompt that made the last 100 queries. |
| `queries.jsonl` | 300 queries with graded answers. |
| `JUDGE.md` | The prompt of the LLM judge. |
| `judgments.jsonl` | The grades of the LLM judge. |

## Queries

Each line is `{"query", "kind", "answers": [{"id", "grade"}]}`. The queries
imitate an AI agent that searches before it knows the name of the definition.
Thus most queries are short and vague. The targets are API definitions, not
helpers, tests or examples.

Two runs wrote the queries. In each run, one agent read the whole dev corpus
and wrote the queries with their answers.

1. The first run wrote 200 queries of 8 kinds for the corpus of 20 packages. It
   used an earlier version of `PROMPT.md`.
2. The second run wrote 100 keywords, phrase and description queries for the
   corpus of 37 packages. It used the current `PROMPT.md`. Agents will mostly
   search with a few words, like a person who searches with Google. These
   three kinds are the main goal of JendHub.

When the corpus grew, 7 packages got a new version. The answers of the first
run now point to the same names in the new versions. In 4 of these answers the
code changed, but the purpose did not, so their grades stayed. The judgments
of the 26 changed definitions were deleted and judged again by pooling.

| Kind | Queries |
|---|---|
| phrase | 67 |
| keywords | 60 |
| description | 54 |
| question | 35 |
| signature | 30 |
| name | 24 |
| exact-name | 15 |
| exact-signature | 15 |

Grades: `3` is exactly what the query asks for, `2` is a good answer, `1` is
related and useful. Each query has at least one grade-3 answer. The queries
hold 1,427 answers: 478 with grade 3, 525 with grade 2 and 424 with grade 1.

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

Pooling costs about $0.15 for 1,000 pairs.

### Judge validation

`bench agreement` judges the original answers blind, and one random
definition for each query. The random definitions come from a fixed seed. The
shares of each judged grade, with medium reasoning, on the first 200 queries
and the corpus of 20 packages:

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

nDCG@10 and judged@10 with the current judgments. The short column is the mean
of the 181 keywords, phrase and description queries, weighted by the number of
queries of each kind.

| Pipeline | nDCG@10 | short | judged@10 |
|---|---|---|---|
| `bm25` | 0.383 | 0.339 | 0.632 |
| `bm25-head-package` | 0.425 | 0.364 | 0.846 |
| `bm25-head-package+name-rrf` | 0.428 | 0.365 | 0.849 |
| `bm25-head-package+name-rerank` | 0.460 | 0.398 | 1.000 |
| `dense-head-package` | 0.683 | 0.686 | 0.805 |
| `dense-text` | 0.731 | 0.735 | 0.977 |
| `dense-text+name-rerank` | 0.740 | 0.741 | 1.000 |
| `dense-text+metadata+name-rerank` | 0.776 | 0.796 | 1.000 |
| `dense-text-summary+name-rerank` | 0.791 | 0.809 | 1.000 |
| `dense-enriched+name-rerank` | 0.794 | 0.819 | 1.000 |
| **`dense-enriched+text+bm25-enriched+role+name-rerank`** | **0.815** | **0.830** | **1.000** |

Rows with judged@10 below 0.95 are not pooled, so their scores are too low.

Main findings:

- Dense search beats BM25 by a large margin on natural-language queries:
  `dense-text+name-rerank` against `bm25-head-package+name-rerank` gains
  +0.253 on keywords, +0.336 on phrase and +0.451 on description. On
  exact-signature queries, the difference is not significant.
- Rank fusion of dense and lexical results made results worse: equal weight
  for a weak list hurts. A linear rerank with small weights works.
- Enrichment improves natural-language queries. `dense-enriched+name-rerank`
  against `dense-text+name-rerank` gains +0.073 on keywords, +0.077 on phrase,
  +0.084 on description and +0.096 on name. Alone it hurts exact-signature
  queries by 0.086.
- The best pipeline adds the code embedding, BM25 and the role to the rerank.
  Against `dense-enriched+name-rerank`, it gains +0.022 on all queries,
  +0.070 on signature, +0.070 on exact-signature and +0.014 on keywords. On
  phrase (+0.014) and description (+0.004), the change is not significant.
- Description is the weakest of the short kinds (0.793). Signature queries
  remain the weakest kind (0.653), but they are not the main goal.

## Weight tuning

The weights of the best pipeline were tuned on all 300 queries for nDCG@10. A
grid search tried 3,234 weight sets for the code, BM25, role and name
features. The weight of the enriched embedding stays 1.0, because only the
ratios change the order.

| Feature | Grid | Best | Flat range |
|---|---|---|---|
| Code embedding | 0 to 1.0 | 0.3 | 0.1 to 0.5 |
| BM25, max-normalized | 0 to 0.2 | 0.075 | 0.05 to 0.1 |
| Role | 0 to 0.1 | 0.03 | 0.02 to 0.05 |
| Name overlap | 0 to 0.3 | 0.05 | 0 to 0.1 |

The best weights gave +0.009 against the old weights (0.5, 0.05, 0.02, 0.1)
on the same queries. The maximum of many weight sets on the same queries is
too high. A 5-fold cross-validation selected the weights on 4 folds and
measured them on the fifth. Over 20 random splits, the gain on the unseen
queries was +0.005 on average, and it was positive in each split.

## Limits

- There is no held-out query set. The weights of the rerankers were tuned on
  all 300 queries, so their gains are a little too high. The best weights form
  a flat plateau, and cross-validation shows a smaller but real gain, which
  limits this risk.
- An LLM wrote the queries, and an LLM writes the enrichment queries. They can
  be more similar to each other than real agent queries are.
- The dev corpus has 5,473 definitions. The search corpus will be much larger,
  with more distractors: the latest releases alone hold about 71,600
  definitions.
