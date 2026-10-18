from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from jendhub.errors import JendHubError
from jendhub.search.searches import Caller
from jendhub.services.app import Running
from jendhub.services.parameters import MAX_LIMIT, MAX_QUERY
from jendhub.services.site.guide import INSTRUCTIONS
from jendhub.services.site.text import SOURCE_TOOL, ToolLinks, render_definition, render_results
from jendhub.services.site.views import EMBEDDING_FAILED, Site

MCP_PATH = "/mcp"
SEARCH_TOOL = "search"
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

type Guard = Callable[[Request], Awaitable[None]]

Query = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_QUERY,
        description="What you need in a few words, for example `decompress gzip data`.",
    ),
]
Limit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="The number of results to give.")]
Offset = Annotated[
    int, Field(ge=0, lt=MAX_LIMIT, description="The number of best results to skip.")
]
SourcePath = Annotated[
    str,
    Field(
        min_length=1,
        description="A file as an import line names it, for example `kit@1.0.0.0/json.bend`."
        " Base is `Base/base.bend`.",
    ),
]
DefinitionName = Annotated[
    str | None,
    Field(description="The name of one definition in the file, for example `parse`."),
]


@contextmanager
def answering() -> Generator[None]:
    try:
        yield
    except HTTPException as error:
        raise ToolError(str(error.detail)) from error
    except JendHubError as error:
        raise ToolError(EMBEDDING_FAILED) from error


def http_request(ctx: Context) -> Request:
    request = ctx.request_context.request
    if not isinstance(request, Request):
        raise ToolError("JendHub answers tool calls only over HTTP.")
    return Request(request.scope)


def client_name(ctx: Context) -> str | None:
    params = ctx.session.client_params
    if params is None:
        return None
    info = params.client_info
    return f"{info.name} {info.version}".strip()


def release_file(path: str) -> tuple[str, str]:
    release, _, file = path.partition("/")
    if not release or not file:
        raise ToolError(f"{path} is not a file of a release, such as kit@1.0.0.0/json.bend.")
    return release, file


def tool_server(running: Running[Site], searching: Guard) -> MCPServer:
    server = MCPServer("JendHub", instructions=INSTRUCTIONS, log_level="WARNING")

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def search(
        query: Query,
        ctx: Context,
        limit: Limit = 10,
        offset: Offset = 0,
    ) -> str:
        """Search the definitions of Base and of the BendHub packages by task or by name.

        Each result gives its import line, the name to call, its head and the source call
        that shows its code.
        """
        with answering():
            request = http_request(ctx)
            await searching(request)
            site = running.get()
            caller = Caller("mcp", request.headers.get("user-agent"), client_name(ctx))
            found, searched = await site.search(query, limit, offset, caller)
            await site.keep(searched)
            return render_results(found, ToolLinks(SEARCH_TOOL, {"query": query, "limit": limit}))

    @server.tool(annotations=READ_ONLY, structured_output=False)
    async def source(path: SourcePath, definition: DefinitionName = None) -> str:
        """Read Bend code from Base or from a release on BendHub.

        Without a definition, the answer is the whole file. With a definition, the answer
        is its code, the import lines that the code uses, and the source calls of the
        definitions that it uses.
        """
        with answering():
            release, file = release_file(path)
            site = running.get()
            if definition is None:
                return (await site.source(release, file)).text
            return render_definition(
                await site.named(release, file, definition), ToolLinks(SOURCE_TOOL)
            )

    return server


@dataclass(frozen=True)
class ToolEndpoint:
    manager: StreamableHTTPSessionManager
    guard: Guard

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.guard(Request(scope))
        await self.manager.handle_request(scope, receive, send)


def tool_route(server: MCPServer, guard: Guard) -> Route:
    server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return Route(MCP_PATH, ToolEndpoint(server.session_manager, guard))
