from jendhub.hub.api import HUB
from jendhub.services.parameters import MAX_LIMIT

ROBOTS = "User-agent: *\nAllow: /\n"


def llms_text(origin: str) -> str:
    return f"""# JendHub

> A search engine for Bend definitions: Base and the latest valid release of
> every package on BendHub ({HUB}).

Each answer is plain text. Links in an answer are full URLs.

## Search

GET {origin}/search?q=<query>&limit=10&offset=0

The query tells what a definition does, or gives its name or a type. The answer
gives the best definitions first, at most {MAX_LIMIT} in total. Each result gives
the name to call, its import line, its doc comment and declaration, and the URL
of its source. When more results exist, the last line gives the URL of the next
results.

## Read

- GET {origin}/definitions/<id> gives the code of a definition with the
  import lines that the code uses, the definitions that it uses, and the
  definitions that use it.
- GET {origin}/files/<release>/<path> gives a file. Each import line in it
  names an exact release, so the file loads from any project. The target of an
  import line is a path under {origin}/files/.
- GET {origin}/files/<release> lists the files of a release. <release> is
  name@version or the hash of the package.
- GET {origin}/packages/<name> gives the releases of a package and its
  definitions.
- GET {origin}/status gives the state of the index and the invalid releases.

## Use a result

Import a result only when it does exactly what you need. Write its import line
at the top of your Bend file and call it by the given name. For example, after
`import kit@1.0.0.0/json.bend as Json`, call `Json.parse(text)`. A definition of
Base needs only `import Base`.

When you are not sure that a result fits, read its source. When a result is
close to what you need, copy its code and its import lines into your project
and change it. You can read and copy all code on BendHub.
"""
