from xml.sax.saxutils import quoteattr

from jendhub.hub.api import HUB

ROBOTS = "User-agent: *\nDisallow: /search?\n"
OPENSEARCH_TYPE = "application/opensearchdescription+xml"


SUMMARY = f"""JendHub is a search engine for the definitions of Base and of the packages on
BendHub ({HUB})."""

QUERIES = """Tell what you need in a few words: the task, some keywords or a short
description, for example `decompress gzip data`, `base64 encode` or
`sha256 hash of bytes`."""

IMPORTS = """Import a result only when it does exactly what you need. Write its import line
at the top of your Bend file and call it by the name that the result gives. For
example, after `import kit@1.0.0.0/json.bend as Json`, call `Json.parse(text)`.
Base needs only `import Base`."""

SOURCES = """When you are not sure that a result fits, read its code. When a result is
close to what you need, copy its code and its import lines into your project
and change it. An import of `./` or `../` names a file of the same release. You
can read and copy all code on BendHub."""

INSTRUCTIONS = f"""{SUMMARY}

Before you write a Bend function or type, use the search tool to find one that
exists. {QUERIES}

{IMPORTS}

{SOURCES}

To read the code of a result, make the read call that the result gives. To find
code in one release, use grep and glob."""


def llms_text(origin: str) -> str:
    summary = "\n".join(f"> {line}" for line in SUMMARY.splitlines())
    return f"""# JendHub

{summary}

Before you write a Bend function or type, search for one that exists:

GET {origin}/search.txt?q=<query>

{QUERIES}

{IMPORTS}

{SOURCES}

Each result links to its code. To read a file, or to find code in one release:

GET {origin}/read.txt?file_path=<name@version>/<path>
GET {origin}/grep.txt?pattern=<regex>&path=<name@version>
GET {origin}/glob.txt?pattern=<glob>&path=<name@version>

Their parameters are those of the Read, Grep and Glob tools of Claude Code, but
grep and glob search only one release.
"""


def opensearch(origin: str) -> str:
    template = quoteattr(f"{origin}/search?q={{searchTerms}}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">\n'
        "<ShortName>JendHub</ShortName>\n"
        "<Description>Search Bend definitions by task or by name</Description>\n"
        "<InputEncoding>UTF-8</InputEncoding>\n"
        f'<Url type="text/html" method="get" template={template}/>\n'
        "</OpenSearchDescription>\n"
    )
