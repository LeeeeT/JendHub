from jendhub.hub.api import HUB

ROBOTS = "User-agent: *\nDisallow: /search?\n"


def llms_text(origin: str) -> str:
    return f"""# JendHub

> Search engine for the definitions of Base and of the packages on BendHub
> ({HUB}).

Before you write a Bend function or type, search for one that exists:

GET {origin}/search.txt?q=<query>

Tell what you need in a few words: the task, some keywords or a short
description, for example `decompress gzip data`, `base64 encode` or
`sha256 hash of bytes`.

Import a result only when it does exactly what you need. Write its import line
at the top of your Bend file and call it by the name that the result gives. For
example, after `import kit@1.0.0.0/json.bend as Json`, call `Json.parse(text)`.
Base needs only `import Base`.

When you are not sure that a result fits, open its source. It shows the code
with its import lines and the definitions that it uses. When a result is close
to what you need, copy its code and its import lines into your project and
change it. You can read and copy all code on BendHub.

To read a whole file, follow its import line:

GET {origin}/src/<name@version>/<path>
"""
