# Write benchmark queries for JendHub

JendHub is a search engine for Bend definitions. AI agents use it to find
definitions in Base and in BendHub packages. Bend is a functional language with
Python-like syntax and dependent types.

Your task is to write 100 search queries for the attached corpus and to grade
the definitions that answer each query. We use your queries to measure search
quality with nDCG.

## The corpus

The attached file contains Base and BendHub packages. Each file starts with a
header line:

- `===== Base =====` starts Base.
- `===== <package>@<version>/<module> =====` starts a package file.

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
  after `type`, `def` or `law`. Form:
  `<package>@<version>/<module>:<definition>`.

Copy the name exactly as the source writes it. Do not add a module, an alias
or a namespace.

## Queries

Write queries that an AI agent could send while it writes Bend code. The agent
knows its task. It does not know which package can help, the name of the
definition, the names of the parameters, or the edge cases that the definition
handles. It searches before it finds the answer. Thus most real queries are
short and vague.

Write each query from the need that the target satisfies, not from the text of
the target. Do not copy details from the comment or the code of the target.
Spread the targets across Base and all packages.

Give each query a `kind` label. Use only these kinds and follow their rules:

- `keywords`: 2 to 5 words, not a sentence. Write about 35 queries.
- `phrase`: 2 to 6 words of natural language. Write about 35 queries.
- `description`: one or two sentences about the task. State the need, not the
  special behavior of the target. Write about 30 queries.

These pairs show queries to avoid and the realistic queries to write instead:

| Kind | Avoid | Prefer |
|---|---|---|
| `keywords` | `JSON object duplicate key last member lookup` | `json get field` |
| `phrase` | `random floating point value from zero inclusive to one exclusive` | `random float` |
| `description` | `Format an instant as UTC RFC 3339 with unnecessary fractional zeros removed.` | `I need to print a timestamp in RFC 3339 format.` |

## Targets

Choose only API definitions as targets. An API definition is a definition that
a user of Base or of the package uses in their own code: a type, a function or
a law that the module offers.

Do not choose these as targets:

- internal helpers
- tests and test data
- examples, demos and `main`
- the steps of a proof that only one other definition uses

Bend has no keyword that marks a definition as public. Use these signs of an
API definition:

- A comment documents it for a user.
- Its name tells its purpose.
- Other packages in the corpus use it.
- A module comment or a section header names it as part of the module.

Use these signs of a helper:

- Its name is short or cryptic.
- Only definitions in the same module use it.
- It does one step of a larger definition.

## Answers

Grade each definition that answers the query:

- `3`: the definition is exactly what the query asks for.
- `2`: the definition is a good answer.
- `1`: the definition is related and useful.

Each query must have at least one answer with grade `3`. A vague query can
have more than one answer with grade `3`.

A vague query fits many definitions. For each query, search the full corpus,
Base included, and grade every definition that an agent with this query could
use. Do not grade only the target. Do not list a definition two times in one
query. Do not write the same query two times.

## Output

Write JSON Lines: one JSON object for each query, one object on each line, and
no other text. Each object has exactly these fields:

```json
{"query": "apply a function to each element of a list", "kind": "description", "answers": [{"id": "List.map", "grade": 3}]}
```

- `query`: the query text.
- `kind`: the kind label.
- `answers`: the graded definitions. Each answer has exactly `id` and `grade`.
