# Design

The search uses the same plan as web search engines: much work when a
definition enters the index, little work for each query. A query costs one
query embedding (approximately $0.0000003) and 10 to 20 ms of local work.

## Corpus

The index holds Base, the standard library from the `main` branch of
`bendlang/bend` (`bend2/base.bend`), and the latest version of the 100 hottest
packages (`corpus.HOTTEST_PACKAGES`). Base ranks before all packages; the
packages rank by `hot`. Definitions with the same signature and doc comment are
one document; the document shows the highest-ranked package that has it.

Index on 2026-10-06: Base (475 definitions) and the latest version of the 100
hottest packages, 74,140 definitions, 55,894 documents. 55,874 documents have an
enrichment; the model left out the other 20.

## Index time

When a definition enters the index (`jend.build`):

1. `deepseek/deepseek-v4-flash` writes an enrichment for the definition
   (`jend.enrich`). It sees the package and its description, the file, the
   names of the definitions in the file (at most 120), and the signature and
   doc comment of each definition. One request holds up to 24 definitions of
   one file. The enrichment holds:
   - the role: `api` (a user imports it), `helper` (a step of another
     definition of the file), `local` (a utility that the file keeps for its
     own code) or `test` (a test, spec, proof, example or usage file);
   - a one-sentence summary;
   - for an API definition, 3 likely queries and 2 or 3 keywords, such as
     `crc32` or `checksum`. Other roles have none, so that helpers do not
     match the words of user queries.

   The enrichment model sometimes returns the list of items without the
   `items` object; `jend.enrich` accepts both forms.

   OpenRouter serves this model through many providers, with different
   prices and with fp4 or fp8 weights. Each request goes only to the
   `open-inference/fp4` endpoint, with no fallback, and each enrichment
   records that endpoint. The enrichments before 2026-10-08 went to the
   provider that OpenRouter selected for each request, which is not known;
   their provider is `null`.
2. `qwen/qwen3-embedding-8b` makes two 1024-dimension vectors (`jend.embed`):
   one of the full document text (name, doc comment, summary, queries,
   keywords, signature, package), and one of the name and the signature only.

Cost of the full index, one time: enrichment $1.28, text vectors $0.07,
signature vectors $0.03. A new definition costs approximately $0.000025.

Then `jend.build` writes the search index (`jend.index`): a BM25 keyword index
of the full text, the documents, and the two vectors as int8 with one scale for
each dimension. Names are split into words.

### Memory

Each query compares its vector with all text vectors, so these stay in memory
(55 MB as int8). Each query reads the other parts only for a few rows, so they
stay on disk: the documents and the BM25 postings in SQLite, and the signature
vectors in a memory-mapped file. The server process needs approximately 150 MB.
The earlier design kept everything in memory as float32 and Python objects:
approximately 680 MB.

int8 against float32, on 164 queries (dev and benchmark): the top result is the
same for every query, and the top 5 is the same set in 98% of the queries. The
benchmark measures did not change (nDCG@10 0.747). float16 was equally exact at
twice the size. Fewer dimensions cost accuracy: 512 dimensions, -0.017 nDCG@10
on the dev set. The scan converts the int8 codes in blocks of 256 rows: the
ranking takes approximately 35 ms for each query, against 10 ms with float32.

## Query time

For each query (`jend.search`):

1. The query vector comes from `queries.sqlite`, or from OpenRouter for a
   new query.
2. Vector search and BM25 each select their best 200 documents. The union is
   the candidate pool, approximately 320 documents. It holds 98% of the
   answers of the benchmark.
3. `jend.score` gives each candidate a score with a fixed formula. The ranking
   holds all candidates in descending order of the score. The package rank
   breaks ties.
4. The engine reads the records of the requested part of the ranking only: 20
   for each page of the HTML list, and 10 for each answer of the text route.
   It keeps the rankings of the 256 most recent queries, so the next page of a
   query takes approximately 2 ms. The ranking is deterministic, so the pages do not repeat
   or skip a result.

### Formula

The formula has no trained parameters. Each weight was set from what the
signal means, before the measurement on the benchmark.

| Part | Value |
| - | - |
| Similarity of the query to the full-text vector | 1.0 x z |
| Similarity of the query to the name and signature vector | 0.5 x z |
| BM25 score of the full text | 0.5 x z |
| Share of the name's words that the query contains | up to +1.0 |
| The name, or its last part, is the query | +1.0 |
| The definition is in Base | +0.25 |
| The name has a helper form (`.go`, `_loop`, `internal_` and similar) | -1.0 |
| The file is a proof, spec, test, example, usage, benchmark or conformance file, but not a shared lemma file (`proofs/lib/`) | -1.0 |
| The definition is a law and the query is not a statement | -0.5 |
| The enrichment gives a role other than `api` | -1.0 |

`z` is the standard score of the signal in the candidate pool: its distance
from the mean of the pool, in standard deviations. So the signals have the
same scale for each query, and a bonus or a penalty of 1.0 moves a candidate
by approximately one standard deviation. A strong match keeps its place after
one penalty; a weak match goes below the others.

The penalties follow rule 8 of the benchmark: only API can be an answer.
The score of a result is the value of the formula. It is not a probability,
and the scores of two queries cannot be compared.

## Results

See [benchmark.md](benchmark.md#results-of-the-current-design).

## Research

This section tells how the design was selected. The measurements use the
benchmark queries q001 to q100, 90 of which have an answer. Costs are for 1000
new queries.

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
| Ranker only (removed) | $0.0003 | 0.797 | 0.700 |
| Formula (current design) | $0.0003 | 0.775 | 0.678 |
| Vector search only | $0.0003 | 0.735 | 0.600 |
| RRF only | $0.0003 | 0.656 | 0.456 |
| BM25 only | $0 | 0.517 | 0.344 |

"Ranker" in this table is a learned ranker (LambdaMART) that the project
removed; see finding 9. Its numbers are optimistic, because it learned from
the benchmark. The project selected a design without Jev: it costs nothing
for each query, and Jev adds +0.08 to +0.12 nDCG@10 for $0.19 or more.

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
9. A learned ranker (LambdaMART on 34 features) got nDCG@10 0.797 in
   cross-validation on the benchmark. But it learned from the benchmark, and
   the benchmark also selected its features and settings, so the number was
   optimistic. It also failed on short queries such as `hash`, because the
   benchmark had none. The project removed it: the benchmark is only for
   evaluation, and a learned ranker needs separate training data. The fixed
   formula gets 0.775 on the same 90 queries, measured one time.
10. The enrichments help: without the summary and the queries in the text,
    nDCG@10 of the formula fell from 0.753 to 0.678 (between 0.040 and 0.075,
    because the run without them had results without a judgment).
11. A role in the enrichment puts the API before helpers. A set of 32 dev
    queries, separate from the benchmark, selected the design: the role
    penalty in addition to the name and file rules was best (nDCG@5 +0.038
    against the earlier enrichment, on a subset of the corpus). On the
    rebuilt index, the role penalty adds +0.036 nDCG@5 (interval +0.022 to
    +0.052) and +0.044 MRR on the benchmark, and +0.032 nDCG@5 on the dev
    set. The role labels are not always right; the model is not
    deterministic at temperature 0. One cause: these requests went to
    different providers, with fp4 or fp8 weights.

## Open questions

- Short queries are the weakest group (`word` nDCG@10 0.608). For `hash`,
  the exact name bonus puts `hash` functions of the package manager `ezx`
  first, which the model labels `api`. A change to the weights needs the dev
  queries to set them, so that the benchmark stays a fair test.
- Training data that is separate from the benchmark, for example Jev labels
  of synthetic queries, can give a learned ranker without the problem of
  finding 9.
- The best score does not separate queries with an answer from queries
  without one (AUC 0.64; Jev had 0.98).
- The formula does not see the bodies of the definitions. A call graph can
  tell a helper that one definition uses from an API.
