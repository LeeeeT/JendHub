# Write benchmark queries for JendHub

JendHub is a search engine for Bend definitions. AI agents use it to find
definitions in Base and in BendHub packages. Bend is a functional language with
Python-like syntax and dependent types.

Your task is to write 200 search queries for the attached corpus and to grade
the definitions that answer each query. We use your queries to measure search
quality with nDCG.

## The corpus

The attached file contains Base and 20 BendHub packages. Each file starts with
a header line:

- `===== Base =====` starts Base.
- `===== <name>@<version>/<module> =====` starts a package file.

Read the full corpus before you write queries.

## Definitions

A definition is a top-level `type`, `def` or `law`. A declaration can start
with `@unsafe`. These are not definitions:

- A constructor. A constructor belongs to its type. If a query asks for a
  constructor, the answer is the type that declares the constructor.
- A `def` that fills a law. The law is the definition.
- A nested `def`, or a line that is not at the start of a line.

## Definition IDs

- A Base definition: its name, as written after `type`, `def` or `law`.
  Example: `List.map`.
- A package definition: the file header, a colon, and the name, as written
  after `type`, `def` or `law`. Example:
  `bend-kit-json@0.5.0.1/json:parse.bytes`.

Copy the name exactly as the source writes it. Do not add a module, an alias
or a namespace.

## Queries

Write queries that an AI agent could send while it writes Bend code. Use many
kinds of queries. For example:

- an exact name or a partial name
- a type signature
- keywords
- a few words
- a question
- a description of a need

Give each query a short `kind` label in lowercase, for example `name`,
`signature`, `keywords`, `phrase`, `question` or `description`. Use the same
label for the same kind of query every time. Use each kind many times.

Write each query for one target definition. Spread the targets across Base and
all 20 packages. Include easy queries and hard queries. A query of a
descriptive kind must describe the need in your own words. It must not contain
the identifiers of the target.

## Answers

Grade each definition that answers the query:

- `3`: the definition is exactly what the query asks for.
- `2`: the definition is a good answer.
- `1`: the definition is related and useful.

Each query must have at least one answer with grade `3`. Search the full
corpus, Base included, for other good answers, and grade them too. Do not list
a definition two times in one query. Do not write the same query two times.

## Output

Write JSON Lines: one JSON object for each query, one object on each line, and
no other text. Each object has exactly these fields:

```json
{"query": "apply a function to each element of a list", "kind": "description", "answers": [{"id": "List.map", "grade": 3}]}
```

- `query`: the query text.
- `kind`: the kind label.
- `answers`: the graded definitions. Each answer has exactly `id` and `grade`.
