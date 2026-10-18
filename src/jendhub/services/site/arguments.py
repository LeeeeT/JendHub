from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jendhub.services.parameters import MAX_LIMIT, MAX_QUERY

MAX_LINES = 2000
MAX_COLUMNS = 2000
MAX_ENTRIES = 1000
MAX_PATHS = 1000
CONTENT_ONLY = 'Requires output_mode: "content", ignored otherwise.'

OutputMode = Literal["content", "files_with_matches", "count"]


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_QUERY,
            description="What you need in a few words, for example `decompress gzip data`.",
        ),
    ]
    limit: Annotated[
        int, Field(ge=1, le=MAX_LIMIT, description="The number of results to give.")
    ] = 10
    offset: Annotated[
        int, Field(ge=0, lt=MAX_LIMIT, description="The number of best results to skip.")
    ] = 0


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: Annotated[
        str,
        Field(
            min_length=1,
            description="The path to the file to read, for example `kit@1.0.0.0/json.bend`",
        ),
    ]
    offset: Annotated[
        int,
        Field(
            ge=0,
            description="The line number to start reading from. Only provide if the file is"
            " too large to read at once",
        ),
    ] = 0
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_LINES,
            description="The number of lines to read. Only provide if the file is too large"
            " to read at once.",
        ),
    ] = MAX_LINES


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Annotated[
        str,
        Field(
            min_length=1,
            description="The regular expression pattern to search for in file contents",
        ),
    ]
    path: Annotated[
        str,
        Field(
            min_length=1,
            description="File or directory to search in (rg PATH): one release, or a file or"
            " directory in it, for example `kit@1.0.0.0` or `kit@1.0.0.0/src`. Base is `Base`.",
        ),
    ]
    glob: Annotated[
        str | None,
        Field(
            description='Glob pattern to filter files (e.g. "*.bend", "src/**") - maps to rg --glob'
        ),
    ] = None
    output_mode: Annotated[
        OutputMode,
        Field(
            description='Output mode: "content" shows matching lines (supports -A/-B/-C'
            ' context, -n line numbers, head_limit), "files_with_matches" shows file paths'
            ' (supports head_limit), "count" shows match counts (supports head_limit).'
            ' Defaults to "files_with_matches".'
        ),
    ] = "files_with_matches"
    before: Annotated[
        int | None,
        Field(
            alias="-B",
            ge=0,
            description=f"Number of lines to show before each match (rg -B). {CONTENT_ONLY}",
        ),
    ] = None
    after: Annotated[
        int | None,
        Field(
            alias="-A",
            ge=0,
            description=f"Number of lines to show after each match (rg -A). {CONTENT_ONLY}",
        ),
    ] = None
    around: Annotated[int | None, Field(alias="-C", ge=0, description="Alias for context.")] = None
    context: Annotated[
        int | None,
        Field(
            ge=0,
            description="Number of lines to show before and after each match (rg -C)."
            f" {CONTENT_ONLY}",
        ),
    ] = None
    line_numbers: Annotated[
        bool,
        Field(
            alias="-n",
            description=f"Show line numbers in output (rg -n). {CONTENT_ONLY} Defaults to true.",
        ),
    ] = True
    ignore_case: Annotated[
        bool, Field(alias="-i", description="Case insensitive search (rg -i)")
    ] = False
    only_matching: Annotated[
        bool,
        Field(
            alias="-o",
            description="Print only the matched (non-empty) parts of each matching line, one"
            f" match per output line (rg -o / --only-matching). {CONTENT_ONLY}"
            " Defaults to false.",
        ),
    ] = False
    multiline: Annotated[
        bool,
        Field(
            description="Enable multiline mode where . matches newlines and patterns can span"
            " lines (rg -U --multiline-dotall). Default: false."
        ),
    ] = False
    head_limit: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_ENTRIES,
            description='Limit output to first N lines/entries, equivalent to "| head -N".'
            " Works across all output modes: content (limits output lines),"
            " files_with_matches (limits file paths), count (limits count entries). Defaults"
            f" to 250 when unspecified. At most {MAX_ENTRIES}.",
        ),
    ] = 250
    offset: Annotated[
        int,
        Field(
            ge=0,
            description="Skip first N lines/entries before applying head_limit, equivalent to"
            ' "| tail -n +N | head -N". Works across all output modes. Defaults to 0.',
        ),
    ] = 0


class GlobArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Annotated[
        str, Field(min_length=1, description="The glob pattern to match files against")
    ]
    path: Annotated[
        str,
        Field(
            min_length=1,
            description="The release to search in, or a directory in it, for example"
            " `kit@1.0.0.0`. Base is `Base`.",
        ),
    ]
    offset: Annotated[
        int,
        Field(ge=0, description=f"Skip first N paths. Gives at most {MAX_PATHS} paths."),
    ] = 0


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]


SEARCH = ToolSpec(
    "search",
    "Search the definitions of Base and of the BendHub packages by task or by name. Each"
    " result gives its import line, the name to call, its head and how to read its code.",
    SearchArguments,
)


def described(summary: str, *notes: str) -> str:
    return "\n".join([summary, "", *(f"- {note}" for note in notes)])


READ = ToolSpec(
    "read",
    described(
        "Reads a file of Base or of a release on BendHub.",
        "`file_path` is the path of an import line, for example `kit@1.0.0.0/json.bend`."
        " Base is `Base/base.bend`.",
        f"Reads up to {MAX_LINES} lines by default.",
        "When you already know which part of the file you need, only read that part. This"
        " can be important for larger files.",
        "Results are returned using cat -n format, with line numbers starting at 1",
        f"A line longer than {MAX_COLUMNS} characters is cut.",
        "Reading a directory or a missing file returns an error rather than content.",
    ),
    ReadArguments,
)
GREP = ToolSpec(
    "grep",
    described(
        "Content search built on ripgrep, in the files of one release.",
        r'Full regex syntax (e.g. "log.*Error", "function\s+\w+"). Ripgrep, not grep —'
        r" escape literal braces (`interface\{\}`).",
        'Filter with `glob` (e.g. "**/*.bend").',
        '`output_mode`: "content" (matching lines), "files_with_matches" (paths only,'
        ' default), or "count".',
        "`multiline: true` for patterns that span lines.",
    ),
    GrepArguments,
)
GLOB = ToolSpec(
    "glob",
    'Fast file pattern matching in one release. Supports glob patterns like "**/*.bend" or'
    ' "src/**/*.bend". Returns matching file paths sorted by path.',
    GlobArguments,
)
TOOLS = [SEARCH, READ, GREP, GLOB]
