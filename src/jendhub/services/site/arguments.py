from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jendhub.services.parameters import MAX_LIMIT, MAX_QUERY

MAX_LINES = 2000
MAX_COLUMNS = 2000
MAX_ENTRIES = 1000
FILE_PATH = (
    "The file, as its import line names it, for example `kit@1.0.0.0/json.bend`. Base is"
    " `Base/base.bend`."
)
RELEASE_PATH = (
    "One release, or a directory or a file in it, for example `kit@1.0.0.0` or"
    " `kit@1.0.0.0/src`. Base is `Base`."
)
LINES_ONLY = "Only for mode `lines`."

Mode = Literal["lines", "files", "counts"]


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_QUERY,
            description="What you need in a few words, for example `decompress gzip data`,"
            " or a name, for example `List.map`.",
        ),
    ]
    limit: Annotated[
        int, Field(ge=1, le=MAX_LIMIT, description="The number of results to give.")
    ] = 10
    offset: Annotated[
        int,
        Field(
            ge=0,
            lt=MAX_LIMIT,
            description="The number of best results to skip, to get the next page.",
        ),
    ] = 0


class DefinitionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, description=FILE_PATH)]
    name: Annotated[
        str,
        Field(
            min_length=1,
            description="The name of the definition in its file, for example `parse` or"
            " `List.map`.",
        ),
    ]


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, description=FILE_PATH)]
    start_line: Annotated[
        int,
        Field(ge=1, description="The number of the first line to give. Line numbers start at 1."),
    ] = 1
    end_line: Annotated[
        int | None,
        Field(
            ge=1,
            description="The number of the last line to give. Without it, the answer goes"
            f" to the end of the file. An answer gives at most {MAX_LINES} lines.",
        ),
    ] = None


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Annotated[
        str,
        Field(
            min_length=1,
            description=r"A regular expression in ripgrep syntax, for example `def parse` or"
            r" `type \w+ is Data`. Escape a literal brace: `Box\{value`.",
        ),
    ]
    path: Annotated[str, Field(min_length=1, description=RELEASE_PATH)]
    glob: Annotated[
        str | None,
        Field(
            description="Search only the files whose paths match this glob, for example"
            " `src/**`. A glob that starts with `!` leaves out the files that match it."
        ),
    ] = None
    mode: Annotated[
        Mode,
        Field(
            description="`lines` gives the matching lines, `files` gives the paths of the"
            " files that match, and `counts` gives the number of matching lines in each file."
        ),
    ] = "lines"
    context: Annotated[
        int | None,
        Field(
            ge=0,
            description=f"The number of lines to show before and after each match. {LINES_ONLY}",
        ),
    ] = None
    before: Annotated[
        int | None,
        Field(ge=0, description=f"The number of lines to show before each match. {LINES_ONLY}"),
    ] = None
    after: Annotated[
        int | None,
        Field(ge=0, description=f"The number of lines to show after each match. {LINES_ONLY}"),
    ] = None
    ignore_case: Annotated[
        bool, Field(description="Match upper case and lower case letters as the same.")
    ] = False
    multiline: Annotated[
        bool,
        Field(description="Let a match go across lines. Then `.` also matches a line end."),
    ] = False
    limit: Annotated[
        int,
        Field(ge=1, le=MAX_ENTRIES, description="The number of lines, paths or counts to give."),
    ] = 250
    offset: Annotated[
        int,
        Field(
            ge=0,
            description="The number of lines, paths or counts to skip, to get the next page.",
        ),
    ] = 0


class FilesArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[
        str,
        Field(
            min_length=1,
            description="One release, or a directory in it, for example `kit@1.0.0.0`. Base"
            " is `Base`.",
        ),
    ]
    pattern: Annotated[
        str,
        Field(
            min_length=1,
            description="Give only the paths that match this glob, for example `src/**`. The"
            " glob matches the path below `path`: `*.bend` matches only the files at its top.",
        ),
    ] = "**"
    limit: Annotated[
        int, Field(ge=1, le=MAX_ENTRIES, description="The number of paths to give.")
    ] = MAX_ENTRIES
    offset: Annotated[
        int, Field(ge=0, description="The number of paths to skip, to get the next page.")
    ] = 0


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: type[BaseModel]


SEARCH = ToolSpec(
    "search",
    "Find the definitions (functions, types and laws) of Base and of the BendHub packages"
    " that do a task, or that have a given name. The best results come first. Each result"
    " gives the"
    " name to call, the import line, the head (doc comment and signature), and the definition"
    " call that opens its code.",
    SearchArguments,
)
DEFINITION = ToolSpec(
    "definition",
    "Open one definition. The answer gives its code, the import lines that the code needs, and"
    " the definitions that the code uses, each with the definition call that opens it. Each"
    " import line names an exact release, so you can copy the code and its import lines into"
    " your project. The answer also gives the read call for the lines of the definition in its"
    " file. The index holds the definitions of Base and of the latest valid release of each"
    " package: read, grep and files work on every release.",
    DefinitionArguments,
)
READ = ToolSpec(
    "read",
    "Read lines of a file of Base or of a release on BendHub. The answer gives each line as its"
    f" number, a tab and its text, and cuts a line after {MAX_COLUMNS} characters. A file shows"
    " its imports as BendHub has them: `./` and `../` name files of the same release. If the"
    " answer stops before the end of the file, its last line gives the read call for the next"
    " lines.",
    ReadArguments,
)
GREP = ToolSpec(
    "grep",
    "Find the lines of one release that match a regular expression, with ripgrep. A matching"
    " line comes as `path:line:text`, and a context line as `path-line-text`, with the path"
    " and the line number that read takes. If more results exist, the last line of the answer"
    " gives the grep call for the next page.",
    GrepArguments,
)
FILES = ToolSpec(
    "files",
    "List the files of one release, or of a directory in it, with the paths that read, grep"
    " and definition take. If more paths exist, the last line of the answer gives the files"
    " call for the next page.",
    FilesArguments,
)
TOOLS = [SEARCH, DEFINITION, READ, GREP, FILES]
