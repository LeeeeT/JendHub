from dataclasses import dataclass
from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jendhub.search.filters import Filters
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
GLOB = (
    "A glob with a `/` matches the path of a file from the top of its release, for example"
    " `src/**`. A glob with no `/` matches the name of a file in any directory, for example"
    " `*.bend`. A glob that starts with `!` leaves out the files that match it."
)
LINES_ONLY = "Only for mode `lines`."
MAX_FILTERS = 10
DEFINITION_KEY = (
    "Name it as `package/module:name`, for example `bend-kit-json/json:parse.bytes`. A"
    " definition of Base is its name, for example `List.map`."
)
REPEAT = "Give it more than once to keep only the definitions that match each value."

Mode = Literal["lines", "files", "counts"]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def without_empty(cls, given: object) -> object:
        if not isinstance(given, dict):
            return given
        fields = cls.model_fields
        return {
            name: without_blanks(value)
            for name, value in cast("dict[str, object]", given).items()
            if value != "" or name not in fields or fields[name].is_required()
        }


def without_blanks(value: object) -> object:
    if not isinstance(value, list):
        return value
    return [item for item in cast("list[object]", value) if item != ""]


Values = list[Annotated[str, Field(min_length=1)]]


class SearchArguments(Arguments):
    query: Annotated[
        str,
        Field(
            max_length=MAX_QUERY,
            description="What you need in a few words. Without it, the results are the"
            " definitions that the filters give, the most used first.",
        ),
    ] = ""
    package: Annotated[
        Values,
        Field(
            default_factory=list,
            max_length=MAX_FILTERS,
            description="Search only the definitions of this package, for example"
            " `bend-kit-json`. Base is `Base`.",
        ),
    ]
    uses: Annotated[
        Values,
        Field(
            default_factory=list,
            max_length=MAX_FILTERS,
            description=f"Search only the definitions that use this definition. {DEFINITION_KEY}"
            f" {REPEAT}",
        ),
    ]
    used_by: Annotated[
        Values,
        Field(
            default_factory=list,
            max_length=MAX_FILTERS,
            description="Search only the definitions that this definition uses."
            f" {DEFINITION_KEY} {REPEAT}",
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

    @property
    def filters(self) -> Filters:
        return Filters(tuple(self.package), tuple(self.uses), tuple(self.used_by))


class DefinitionArguments(Arguments):
    path: Annotated[str, Field(min_length=1, description=FILE_PATH)]
    name: Annotated[
        str,
        Field(
            min_length=1,
            description="The name of the definition as its file declares it, for example"
            " `parse` or `List.map`. Give it with no alias of an import line: `parse`, not"
            " `Json.parse`.",
        ),
    ]


class ReadArguments(Arguments):
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


class GrepArguments(Arguments):
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
        Field(description=f"Search only the files that match this glob. {GLOB}"),
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
        Field(
            ge=1,
            le=MAX_ENTRIES,
            description="The number of lines to give. Each match, context line, `--`, path or"
            " count is one line.",
        ),
    ] = 250
    offset: Annotated[
        int, Field(ge=0, description="The number of lines to skip, to get the next page.")
    ] = 0


class FilesArguments(Arguments):
    path: Annotated[
        str,
        Field(
            min_length=1,
            description="One release, or a directory in it, for example `kit@1.0.0.0`. Base"
            " is `Base`.",
        ),
    ]
    glob: Annotated[
        str | None, Field(description=f"List only the files that match this glob. {GLOB}")
    ] = None
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
    "Find the definitions (functions, types and laws) of Base and of the BendHub packages."
    " The best results come first. Each result gives the name to call, the import line,"
    " the head (doc comment and signature), and the `definition` call that opens its code.",
    SearchArguments,
)
DEFINITION = ToolSpec(
    "definition",
    "Open one definition. The answer gives its code, the import lines that the code needs, and"
    " the definitions that the code uses, each with the `definition` call that opens it. Each"
    " import line names an exact release, so you can copy the code and its import lines into"
    " your project. The answer also gives the `read` call for the lines of the definition in its"
    " file. `definition` opens only the definitions of Base and of the latest valid release of"
    " each package, because only they are in the index. `read`, `grep` and `files` work on every"
    " release.",
    DefinitionArguments,
)
READ = ToolSpec(
    "read",
    "Read lines of a file of Base or of a release on BendHub. The answer gives each line as its"
    f" number, a tab and its text, and cuts a line after {MAX_COLUMNS} characters. If the"
    " answer stops before `end_line`, or before the end of the file when you give no"
    " `end_line`, its last line gives the `read` call for the next lines. The file shows its"
    " imports as BendHub stores them: `./` and `../` name files of the same release. To copy"
    " code, take its import lines from `definition`.",
    ReadArguments,
)
GREP = ToolSpec(
    "grep",
    "Find the lines of one release that match a regular expression, with ripgrep. In mode"
    " `lines`, a matching line comes as `path:line:text` and a context line as"
    " `path-line-text`, with the path and the line number that `read` takes. A line `--`"
    " separates groups of lines that are not next to each other. Mode `files` gives one path"
    " on each line, and mode `counts` gives `path:count`. If more results exist, the last line"
    " of the answer gives the `grep` call for the next page.",
    GrepArguments,
)
FILES = ToolSpec(
    "files",
    "List the files of one release, or of a directory in it, with the paths that `read`,"
    " `grep` and `definition` take. If more paths exist, the last line of the answer gives the"
    " `files` call for the next page.",
    FilesArguments,
)
TOOLS = [SEARCH, DEFINITION, READ, GREP, FILES]
