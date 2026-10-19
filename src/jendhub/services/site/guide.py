import json
from typing import Any
from xml.sax.saxutils import quoteattr

from jendhub.hub.api import HUB
from jendhub.services.site.arguments import TOOLS, ToolSpec

ROBOTS = "User-agent: *\nDisallow: /search?\n"
OPENSEARCH_TYPE = "application/opensearchdescription+xml"


SUMMARY = (
    "JendHub is a search engine for the definitions of Base and of the packages on BendHub"
    f" ({HUB})."
)
SEARCH_FIRST = (
    "Before you write a Bend function or type, use the search tool to find one that exists."
    " Tell what you need in a few words: the task, some keywords or a short description, for"
    " example `decompress gzip data`, `base64 encode` or `sha256 hash of bytes`."
)
IMPORTS = (
    "Import a result only when it does exactly what you need. Write its import line at the"
    " top of your Bend file and call it by the name that the result gives. For example, after"
    " `import kit@1.0.0.0/json.bend as Json`, call `Json.parse(text)`. Base needs only"
    " `import Base`."
)
SOURCES = (
    "When you are not sure that a result fits, read its code. When a result is close to what"
    " you need, copy its code and its import lines into your project and change it. An import"
    " of `./` or `../` names a file of the same release. You can read and copy all code on"
    " BendHub."
)
FOLLOW = (
    "To read the code of a result, make the read call that the result gives. To find code in"
    " one release, use grep and glob."
)
TOOLS_INTRO = (
    "Each tool is a GET path that takes its parameters in the query string. The answer is"
    " plain text, also for an error."
)

INSTRUCTIONS = f"{SUMMARY}\n\n{SEARCH_FIRST}\n\n{IMPORTS}\n\n{SOURCES}\n\n{FOLLOW}"


def kind(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(kind(part) for part in schema["anyOf"] if part.get("type") != "null")
    return schema["type"]


def bounds(schema: dict[str, Any]) -> list[str]:
    found = next((part for part in schema.get("anyOf", []) if "minimum" in part), schema)
    least = found.get("minimum")
    most = found.get("maximum")
    if "exclusiveMaximum" in found:
        most = found["exclusiveMaximum"] - 1
    if least is not None and most is not None:
        return [f"{least} to {most}"]
    if least is not None:
        return [f"at least {least}"]
    if "maxLength" in found:
        return [f"at most {found['maxLength']} characters"]
    return []


def parameter(name: str, schema: dict[str, Any], *, required: bool) -> str:
    default = schema.get("default")
    given = (
        "required"
        if required
        else "optional"
        if default is None
        else f"default {json.dumps(default)}"
    )
    facts = ", ".join([kind(schema), *bounds(schema), given])
    return f"- `{name}` ({facts}): {schema['description']}"


def tool_section(origin: str, spec: ToolSpec) -> str:
    schema = spec.arguments.model_json_schema()
    required: list[str] = schema.get("required", [])
    query = "&".join(f"{name}=<{name}>" for name in required)
    parameters = "\n".join(
        parameter(name, field, required=name in required)
        for name, field in schema["properties"].items()
    )
    return f"""### {spec.name}

GET {origin}/{spec.name}.txt?{query}

{spec.description}

Parameters:

{parameters}
"""


def llms_text(origin: str) -> str:
    tools = "\n".join(tool_section(origin, spec) for spec in TOOLS)
    return f"""# JendHub

> {SUMMARY}

{SEARCH_FIRST}

{IMPORTS}

{SOURCES}

## Tools

{TOOLS_INTRO}

{tools}"""


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
