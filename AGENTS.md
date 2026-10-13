# JendHub - a Search Engine for Bend Definitions

## Rules

### 1. Select quality before stability

This repository has no compatibility requirements. Make a breaking change when
it gives the system a better design. Change the architecture or add a dependency
when this improves the result.

Do not keep an inferior design because a stable interface uses it. When a new
design replaces an old design, remove the old design completely. Do not keep
compatibility routes, deprecated names, migration shims, optional legacy modes,
or a second path that implements the old behavior.

### 2. Remove ambiguity before work starts

Make the task unambiguous before you change files. Ask questions
until each answer that can change the design or implementation is known.

Do not replace missing information with a guess. Do not start work with an
assumption when the user's answer can invalidate that work.

### 3. Derive the design from the objective

For every decision, ask what the best expert in that field would do and why they
would reject your current choice. If you can name that reason, don't make the
choice. Optimize for what that expert would judge correct, never for what
satisfies the stated constraints most cheaply.

### 4. Use controlled prose

Use ASD-STE100 in messages, commit messages, documentation, and code comments.
Use short sentences. Use active voice. Give one meaning to each sentence.
Avoid code comments. Use names and structure to explain the code.

### 5. Deliver one part at a time

Build one self-contained part, then stop for review. Commit only when the user
says "commit". Ask the user about decisions that change the design or cost
money, for example a model, a provider or the pipelines in a pool. Decide the
small details yourself.

### 6. Measure each change with the benchmark

Keep each experiment as a separate pipeline in `PIPELINES`. Pool a new
pipeline with `jendhub bench pool` before you compare it. Compare pipelines with
`jendhub bench compare`, and report the change with its 95% interval, not only
two scores. Trust a comparison only when judged@10 is about 0.95 or more for
both pipelines. Read [bench/README.md](bench/README.md) before you change the
benchmark.

### 7. Keep the documentation current

When a change alters a command, a design decision, a model or a result, update
[README.md](README.md) and [bench/README.md](bench/README.md) in the same
commit.

### 8. Protect the secrets

`.env` holds `OPENROUTER_API_KEY`. Never print or commit its value.
