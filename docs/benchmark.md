# Benchmark

`data/benchmark.json` measures the search. `python -m jend.evaluate` runs it
(see the README). This document tells how the benchmark was made, how to grade
new results, and what the current design gets.

## Queries

The 100 queries are in the style of LLM coding agents that write Bend
programs. They do not contain typos or half-remembered names. Each query has a
`style` and a `topic`.

| Style | Queries | Example |
| - | - | - |
| task | 56 | `parse an ISO 8601 date-time string with a UTC offset` |
| keywords | 17 | `HMAC-SHA256` |
| statement | 11 | `reverse (reverse xs) == xs` |
| identifier | 7 | `Map.get` |
| signature | 7 | `Maybe<A> -> A -> A` |
| question | 2 | `how do I read the whole contents of a file into a string?` |

The topics cover Base (lists, strings, numbers, maps, Maybe and Result, IO,
files, threads and channels, TCP), the libraries of the indexed packages
(JSON, HTTP, URL, encodings, cryptography, compression, time, CLI, TOML,
SQLite, webhooks, LLM SDKs, random numbers, containers, BLAS, tracing,
Unicode) and laws about Nat, List, Bool and equality. 10 queries have no answer
in the corpus. 8 of them ask for something that no indexed package does
(PostgreSQL, regex, XML, SMTP, PNG, WebSocket, YAML, edit distance).

## Judgments

The judgments use the TREC method:

1. A pool of candidates for each query: the union of the best 20 results of
   BM25, of vector search and of the Jev ranking, 37 documents on average.
2. A judge grades each candidate from its signature, doc comment and body.
3. The judge searches the corpus for answers that are not in the pool and
   adds them. This added 67 answers that no retriever found.
4. One review applied rule 8 below to all queries, and changed 133 grades
   to 1.

Claude Opus 5.5 did the grading with the rules below, in 2026-10. There are
3728 judgments: 1832 of grade 0, 1510 of grade 1, 204 of grade 2 and 182 of
grade 3. The `note` of a judgment gives the reason for its grade.

A result that has no judgment counts as grade 0. `jend.evaluate` lists the
results in the top 10 that have no judgment. When a new design returns many of
them, grade them with the rules below and add them to `data/benchmark.json`.
Otherwise the measurement is low for the new design.

## Grades

**3: Answer.** The agent can use the definition directly to do what the query
asks.
- Task, question or keyword query: calling or using the definition does the
  job.
- Statement query: the law states the fact, or an equivalent form (sides
  swapped, variables renamed, specialised to the type that the query names).
- Signature query: the signature matches, up to the names of type variables
  and the order of arguments, and the definition does what such a signature
  most naturally means.
- Identifier query: the definition has that name, or it is the obvious
  equivalent in another package.
- Type request: the type that represents it.

**2: Partial answer.** The agent can do the task with small extra work, or the
definition does the central part of the request: the same operation over a
different but convertible type, a more general or a more specific variant, or
a public function that does the main step of a request with many steps.

**1: Related.** An internal helper of an answer (`.go`, `.step`, `.if`,
`internal_...`), the type of an answer, a law about an answer (or the function
of a law), an example or a test of an answer, the inverse operation, or a
lemma that the asked fact directly depends on.

**0: Not relevant.**

## Rules

1. Grade each definition on its own. The same definition in two packages gets
   the same grade.
2. The summary is machine-written and can be wrong. The signature, the doc
   comment and the body decide.
3. A query can have no answer. Do not raise a grade to give it one.
4. Grade what the query asks, as an agent reads it.
5. An `IO` function and a pure function that both do the job both get 3.
6. Search the corpus for answers that are not in the pool.
7. Definitions that do the same thing for one query get the same grade.
8. Only API can be an answer. API is a definition that a user of the package
   imports for its own purpose. These have the maximum 1:
   - a local utility: a small general function that a file defines for its
     own code, often a copy of a Base function;
   - a `def` or `type` in a proof, spec, test, example, usage, benchmark or
     conformance file, unless the package exists to export it;
   - for a statement query, a local lemma that one proof keeps for its own
     use, or a law named `internal_...`. Laws of Base, of a law library such
     as bend-mathlib, and of shared lemma files such as `proofs/lib/nat.bend`
     are answers.

Rule 8 keeps the ideal ranking clean. Without it, `Maybe<A> -> A -> A` had 12
answers of grade 3, and 11 of them were copies in proof files. nDCG then
rewards a ranking that shows the copies instead of `Maybe.default`.

## Results of the current design

Two runs of the current design on 2026-10-06 (index `4b649a6c9fe2a7ac`):

| Measure | Run 1 | Run 2 |
| - | - | - |
| nDCG@10 | 0.879 | 0.884 |
| Grade 3 first | 0.800 | 0.822 |
| MRR | 0.936 | 0.946 |
| Recall@20 | 0.901 | 0.896 |
| AUC of the best score for "has an answer" | 0.980 | |
| Cost | $0.053 | $0.053 |
| Latency p50, p95 | 1.8 s, 18.5 s | |

The two runs differ only because Jev gives slightly different probabilities,
and the query embeddings from OpenRouter also change a little between calls.
A difference of less than approximately 0.01 nDCG@10 or 0.03 in "grade 3
first" is noise.

Share of the answers (grade 2 or 3) that each stage finds:

| Stage | @10 | @25 | @50 | @100 |
| - | - | - | - | - |
| BM25 | 0.486 | 0.644 | 0.729 | 0.828 |
| Vector search | 0.674 | 0.859 | 0.909 | 0.945 |
| Fused candidates | 0.614 | 0.788 | 0.912 | 0.912 |
| Jev ranking | 0.858 | 0.903 | 0.912 | 0.912 |

BM25 brings 16 answers into the candidates that vector search does not have in
its best 50, for example the only grade 3 answer of `compare two byte strings
in constant time`. The fusion removes 14 answers that vector search has in its
best 50, for example both answers of `HMAC-SHA256`. The candidates contain 50
documents. 6 of the 386 answers are at ranks 51 to 100 of vector search and do
not get to Jev.

The weakest style is `signature` (nDCG@10 0.744). The best score for a query
without an answer is 0.61, and the lowest best score for a query with an
answer is 0.38. So no threshold separates them completely.
