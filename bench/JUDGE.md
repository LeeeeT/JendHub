# Grade a search result for JendHub

JendHub is a search engine for Bend definitions. AI agents use it to find
definitions in Base and in BendHub packages while they write Bend code. Bend is
a functional language with Python-like syntax and dependent types.

You get one search query and one definition from the search results. Grade how
well the definition answers the query.

## The query

The agent knows its task. It does not know which package can help or the name
of the definition. Thus most queries are short and vague. The query kind tells
how the agent wrote the query:

- `name`: a guessed or partial identifier.
- `signature`: approximate types.
- `keywords`: 2 to 5 words.
- `phrase`: 2 to 6 words of natural language.
- `question`: one short question about the need.
- `description`: one or two sentences about the task.
- `exact-name`: the exact name of a definition that the agent saw before.
- `exact-signature`: the exact signature of a definition that the agent saw
  before.

## The definition

A definition is a top-level `type`, `def` or `law`. A type includes its
constructors. If a query asks for a constructor, the type that declares the
constructor answers it.

The ID of a Base definition is its name, for example `List.map`. The ID of a
package definition is `<name>@<version>/<module>:<name>`, for example
`bend-kit-json@0.5.0.1/json:parse.bytes`.

## Grades

- `3`: the definition is exactly what the query asks for. A vague query can
  have more than one definition with grade `3`.
- `2`: the definition is a good answer. The agent can use it for its need, but
  it is not exactly what the query asks for. For example, it is a more general
  or a more specific variant.
- `1`: the definition is related and useful. It does not satisfy the need, but
  it helps the agent. For example, it is the type that the needed function
  works on, or it does one part of the need.
- `0`: the definition does not help the agent with this query.

For an `exact-name` or an `exact-signature` query, give grade `3` only to the
definition with that name or signature.

## API definitions and helpers

Agents look for API definitions. An API definition is a type, a function or a
law that a module offers to its users. Use these signs of an API definition:

- A comment documents it for a user.
- Its name tells its purpose.

Use these signs of an internal helper:

- Its name is short or cryptic.
- It does one step of a larger definition.

Tests, test data, examples, demos and `main` are not API definitions. Give at
most grade `1` to a helper, a test, an example, a demo or `main`.

## Output

Return only a JSON object with the grade, for example `{"grade": 2}`.
