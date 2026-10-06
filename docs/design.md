# Design

The search uses the same plan as web search engines: much work when a
definition enters the index, little work for each query. A query costs one
query embedding (approximately $0.0000003) and 10 to 20 ms of local work.

## Corpus

The index holds Base, the standard library from the `main` branch of
`bendlang/bend` (`bend2/base.bend`), and the latest version of the 50 hottest
packages (`corpus.HOTTEST_PACKAGES`). Base ranks before all packages; the
packages rank by `hot`. Definitions with the same signature and doc comment are
one document; the document shows the highest-ranked package that has it.

Index: Base (463 definitions) and the latest version of the 50 hottest
packages, 53,511 definitions, 43,952 documents. All documents have a summary.

## Index time

When a definition enters the index (`jend.index`):

1. `deepseek/deepseek-v4-flash` writes a one-sentence summary and 3 likely
   queries for the definition, from its signature, doc comment, file and
   package description (`jend.enrich`). One request holds up to 24
   definitions of one file. The enrichment model sometimes returns the list of
   items without the `items` object; `jend.enrich` accepts both forms.
2. `qwen/qwen3-embedding-8b` makes two 1024-dimension vectors (`jend.embed`):
   one of the full document text (name, doc comment, summary, queries,
   signature, package), and one of the name and the signature only.

Cost of the full index, one time: enrichment approximately $0.95, text vectors
$0.06, signature vectors $0.03. A new definition costs approximately $0.00002.

When the index loads, the server builds a BM25 keyword index of the full text,
and one BM25 index for each field: name, doc comment, enrichment, signature
and location (package, file and package description). Names are split into
words.

## Query time

For each query (`jend.search`):

1. The query vector comes from `embeddings.sqlite`, or from OpenRouter for a
   new query.
2. Vector search and BM25 each select their best 200 documents. The union is
   the candidate pool, approximately 320 documents. It holds 98% of the
   answers of the benchmark.
3. `jend.features` computes 34 features for each candidate.
4. The ranker (`jend.ranker`) gives each candidate a score. The engine returns
   the best 50 in descending order of the score. The package rank breaks ties.
5. A logistic function changes the score into the probability that the
   candidate is an answer (grade 2 or 3).

### Features

| Group | Features |
| - | - |
| Vector search | cosine, distance to the best cosine, log of the rank |
| BM25 | score relative to the best, distance to the best, log of the rank |
| BM25 for each field | score relative to the best candidate, for the 5 fields |
| Signature vector | cosine, distance to the best candidate |
| Query and name | share of query words in the name, name equals the query |
| Query and type | overlap of the type names, when the query contains `->` |
| Query form | statement query, signature query, law for a statement query |
| File | proof file, shared proof library, spec file, test or example file |
| Name | helper form (`.go`, `_loop`, `internal_` and similar), number of dots |
| Definition | `law`, `type`, has a doc comment, doc length, signature length |
| Package | Base, package rank, number of packages with a copy |
| Enrichment | no summary |

The file, name and package features let the ranker put the API before the
copies, the helpers and the proofs that grading rule 8 limits to grade 1 (see
[benchmark.md](benchmark.md)).

### Ranker

The ranker is LambdaMART (LightGBM, 200 trees with 7 leaves, gains 0, 1, 3 and
7 for the grades). `python -m jend.ranker` trains it on all benchmark queries.
The calibration is a logistic fit on the out-of-fold scores of the best 20
candidates of each query. `src/jend/ranker.json` holds the trees, the feature
names and the calibration. The server refuses a model with other feature names.

`jend.evaluate` measures the ranker with 5-fold cross-validation over the
queries, so no query is ranked by a model that learned from it.

## Results

See [benchmark.md](benchmark.md#results-of-the-current-design).

## Research

This section tells how the design was selected. The measurements use the
benchmark with 90 queries that have an answer. Costs are for 1000 new queries.

### Pareto frontier

The earlier design sent the 50 best candidates of reciprocal rank fusion (RRF)
to Jev, a decision model from TypeSafe ($0.042 per million input tokens). Jev
asked one `noul` for each candidate: "Would a programmer call definition
`definitions.d07` directly to do what `query` asks for?".

| Design | Cost | nDCG@10 | Grade 3 first |
| - | - | - | - |
| RRF, then Jev `noul` on 50 (earlier design) | $0.533 | 0.881 | 0.811 |
| Ranker, then Jev `noul` on 30 | $0.322 | 0.879 | 0.844 |
| Ranker, then Jev `noul` on 20, lean cards | $0.190 | 0.875 | 0.822 |
| Ranker, then Jev `noul` on 15, lean cards | $0.145 | 0.861 | 0.822 |
| Ranker, then one Jev `choice` over 20 | $0.117 | 0.852 | 0.822 |
| Ranker only (current design) | $0.0003 | 0.797 | 0.700 |
| Vector search only | $0.0003 | 0.735 | 0.600 |
| RRF only | $0.0003 | 0.656 | 0.456 |
| BM25 only | $0 | 0.517 | 0.344 |

The ranker numbers with Jev used an earlier ranker that had nDCG@10 0.794.
The project selected the ranker without Jev: it costs nothing for each query,
and Jev adds +0.08 nDCG@10 for $0.19 or more.

### Findings

1. RRF made the order worse than vector search alone (0.656 against 0.735),
   because BM25 puts helpers and proof files high. RRF needed 50 candidates to
   keep the answers for Jev. The ranker has more answers in its best 20 than
   RRF has in its best 20 (0.84 against 0.75).
2. Jev cost is linear in the number of candidates. One card costs
   approximately 115 to 150 tokens. One `noul` costs approximately 97 tokens;
   its criteria are approximately 70 of them, because each question repeats
   them. With fewer than 20 candidates, the loss is larger than the noise.
3. The format of the card and the question changes nDCG@10 by only 0.01 to
   0.02. Criteria in the state, written one time, decrease the tokens by 24%
   and nDCG@10 by approximately 0.01.
4. One `choice` over all candidates removes the cost of the questions. Its
   probability goes to the best 3 candidates, so the order after them comes
   from the ranker.
5. Jev gave 0.872 on the ranker's best 20, where a perfect order gives 0.928.
   Most of the difference is in fine API decisions: a local copy of
   `hex_word` before `I32.show_radix`, or the functions of a container before
   its type.
6. These ideas did not help: a fusion of the Jev probability with the ranker
   score (+0.005, noise); Jev only when the ranker is not certain (the curve is
   almost linear: Jev on 75% of the queries gives 0.862); a local
   cross-encoder (Qwen3-Reranker-0.6B needs 30 to 90 s for each query on a
   CPU).
7. The signature vector gives most of the gain of the extra vectors
   (+0.021 nDCG@10, interval +0.008 to +0.034). Vectors of the summary and of
   each generated query add less than the noise. A signature vector cut to 256
   dimensions loses 0.007.
8. Two Jev runs of the same requests differ by approximately 0.002 nDCG@10.

## Open questions

- The ranker learns from 100 queries. More judged queries, in particular of
  the `task` and `signature` styles, can make it better and make the
  measurement more exact.
- The best score separates queries with an answer from queries without one
  less well than Jev did (AUC 0.81 against 0.98). The page shows all results;
  a threshold needs more queries without an answer.
- The features do not see the bodies of the definitions. A call graph can tell
  a helper that one definition uses from an API.
