# Benchmark

`data/benchmark.json` measures the search. `python -m jend.evaluate` runs it
(see the README). This document tells how the benchmark was made, how to grade
new results, and what the current design gets.

The benchmark is only for evaluation. Do not train a model on it, and do not
tune weights or patterns to make its numbers higher. A design that learned
from the benchmark gets numbers that are higher than its real quality, and
the benchmark can then no longer compare designs. Set the parameters of a
design first, then measure one time.

## Queries

The 112 queries are in the style of LLM coding agents that write Bend
programs. They do not contain typos or half-remembered names. Each query has a
`style` and a `topic`.

| Style | Queries | Example |
| - | - | - |
| task | 47 | `parse an ISO 8601 date-time string with a UTC offset` |
| word | 21 | `hash` |
| keywords | 19 | `HMAC-SHA256`, `hash bytes` |
| identifier | 10 | `Map.get` |
| statement | 7 | `reverse (reverse xs) == xs` |
| signature | 6 | `Maybe<A> -> A -> A` |
| question | 2 | `how do I read the whole contents of a file into a string?` |

Queries q001 to q100 were written first. Queries q101 to q132 are short
queries (one word, two or three words, or a bare name) that were added after
a manual test showed that the first set had no query such as `hash`.

On 2026-10-09, 20 queries were removed, because some of their answers are in
files that Bend rejects, and the mirror does not keep such files: q007, q030,
q042 to q046, q049, q051, q067, q076, q082 to q084, q086, q102, q104, q105,
q114 and q122.

The topics cover Base (lists, strings, numbers, maps, Maybe and Result, IO,
files, threads and channels, TCP), the libraries of the indexed packages
(JSON, HTTP, URL, encodings, cryptography, compression, time, CLI, TOML,
SQLite, webhooks, LLM SDKs, random numbers, containers, BLAS, tracing,
Unicode) and laws about Nat, List, Bool and equality. 8 queries have no answer
in the corpus. With the 100 hottest packages, the queries about regular
expressions, SMTP and PNG got answers. 8 of them ask for something that no indexed package does
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
5. Later designs put results without a judgment in their top 10. The judge
   graded them with the same rules (39 judgments).

When the index moved to the latest versions of the 100 hottest packages
(2026-10-06), a judgment of a definition that changed moved to its new version
(same package, file and name; 50 judgments), and judgments of definitions that
are no longer in the corpus were removed (68). The judge then graded the 495
new results in the top 10 of the formula with and without the role penalty.

For q101 to q132, the pool is the union of the best 20 results of BM25, of
vector search, of the ranking formula and of the Jev design, 40 documents on
average. A search of the corpus by name added 88 answers and related
definitions. The judge saw the signature, the doc comment, the summary and
the file of each definition, not its body.

Claude Opus 5.5 did the grading with the rules below, in 2026-10. There are
5549 judgments: 2229 of grade 0, 2497 of grade 1, 430 of grade 2 and 393 of
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
- Word query: a public definition whose main purpose is what the word names
  in its most common meaning for programmers (`hash`: a function that hashes
  data). When two meanings are equally common, both are answers (`map`: the
  `Map` type and `List.map`).

**2: Partial answer.** The agent can do the task with small extra work, or the
definition does the central part of the request: the same operation over a
different but convertible type, a more general or a more specific variant, or
a public function that does the main step of a request with many steps. For
a word query: a less common meaning (`hash`: the `HashMap` type).

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

Run of 2026-10-09 on the index of Base and the latest versions of the 100
hottest packages (index `002af78b7b5be9f5`). The mirror of this index reads
the files with the port of Bend's parser and leaves out the files that Bend
rejects.

| Measure, 104 queries with an answer | Formula |
| - | - |
| nDCG@10 | 0.748 |
| Grade 3 first | 0.673 |
| MRR | 0.843 |
| Recall@20 | 0.824 |

On the same 104 queries, the run of 2026-10-06 (index `75cb8f3f48f45154`,
before the port) gets 0.741 nDCG@10: +0.007 (95% interval +0.001 to +0.014;
better on 31 queries, worse on 18). On that run, the role penalty added
+0.036 nDCG@5 (95% interval +0.022 to +0.052).

Share of the answers (grade 2 or 3) that each stage finds:

| Stage | @10 | @25 | @50 | @100 |
| - | - | - | - | - |
| BM25 | 0.514 | 0.642 | 0.731 | 0.815 |
| Vector search | 0.570 | 0.754 | 0.852 | 0.919 |
| Formula | 0.712 | 0.848 | 0.927 | 0.958 |

nDCG@10 for each style: `identifier` 0.879, `statement` 0.808, `task` 0.772,
`question` 0.748, `keywords` 0.737, `signature` 0.657, `word` 0.648.

The worst queries: `hash` (the `hash` functions of the package manager `ezx`
come first), `hex` (local hex utilities), `parse int`, and `unicode general
category of a code point` (table entries come first). The best score does
not separate queries with an answer from queries without one (AUC 0.66).

## Dev queries

`data/dev.json` holds 32 other queries, graded with the same rules (1,291
judgments). Use them to choose between designs and to set weights or prompts.
Then measure the chosen design one time on the benchmark. `python -m
jend.evaluate --benchmark data/dev.json` measures them.
