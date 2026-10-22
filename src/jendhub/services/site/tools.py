from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.caching import CacheHint
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from jendhub.remote.openrouter import OpenRouterError
from jendhub.search.searches import Caller
from jendhub.services.app import Running
from jendhub.services.parameters import invalid_parameters
from jendhub.services.site.arguments import (
    DEFINITION,
    FILES,
    GREP,
    READ,
    SEARCH,
    DefinitionArguments,
    FilesArguments,
    GrepArguments,
    ReadArguments,
    SearchArguments,
)
from jendhub.services.site.arguments import TOOLS as SPECS
from jendhub.services.site.guide import INSTRUCTIONS
from jendhub.services.site.text import (
    ToolLinks,
    render_definition,
    render_lines,
    render_matches,
    render_paths,
    render_results,
)
from jendhub.services.site.views import EMBEDDING_FAILED, Site

MCP_PATH = "/mcp"
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
STATIC = CacheHint(ttl_ms=3_600_000, scope="public")

type Guard = Callable[[Request], Awaitable[None]]
type Context = ServerRequestContext[Any, Any]
type Answer = Callable[[Context, dict[str, Any]], Awaitable[str]]

TOOLS = [
    Tool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.arguments.model_json_schema(),
        annotations=READ_ONLY,
    )
    for spec in SPECS
]
SCHEMAS = {tool.name: tool.input_schema for tool in TOOLS}


class ToolCallError(Exception):
    pass


@contextmanager
def answering() -> Generator[None]:
    try:
        yield
    except ValidationError as error:
        raise ToolCallError(invalid_parameters(error.errors())) from error
    except HTTPException as error:
        raise ToolCallError(str(error.detail)) from error
    except OpenRouterError as error:
        raise ToolCallError(EMBEDDING_FAILED) from error


def http_request(ctx: Context) -> Request:
    request = ctx.request
    if not isinstance(request, Request):
        raise ToolCallError("JendHub answers tool calls only over HTTP.")
    return Request(request.scope)


def client_name(ctx: Context) -> str | None:
    params = ctx.session.client_params
    if params is None:
        return None
    info = params.client_info
    return f"{info.name} {info.version}".strip()


def tool_links(tool: str, given: BaseModel) -> ToolLinks:
    given_values = given.model_dump(exclude_unset=True, exclude_none=True)
    return ToolLinks(tool, {name: value for name, value in given_values.items() if value != []})


def answer(text: str, *, failed: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(text=text)], is_error=failed)


def tool_server(running: Running[Site], searching: Guard, version: str) -> Server[Any]:
    async def search(ctx: Context, arguments: dict[str, Any]) -> str:
        given = SearchArguments.model_validate(arguments)
        request = http_request(ctx)
        await searching(request)
        site = running.get()
        caller = Caller("mcp", request.headers.get("user-agent"), client_name(ctx))
        found, searched = await site.search(
            given.query, given.filters, given.limit, given.offset, caller
        )
        await site.keep(searched)
        return render_results(found, tool_links(SEARCH.name, given))

    async def definition(_: Context, arguments: dict[str, Any]) -> str:
        given = DefinitionArguments.model_validate(arguments)
        view = await running.get().named(given)
        return render_definition(view, tool_links(DEFINITION.name, given))

    async def read(_: Context, arguments: dict[str, Any]) -> str:
        given = ReadArguments.model_validate(arguments)
        return render_lines(await running.get().read(given), tool_links(READ.name, given))

    async def grep(_: Context, arguments: dict[str, Any]) -> str:
        given = GrepArguments.model_validate(arguments)
        return render_matches(await running.get().grep(given), tool_links(GREP.name, given))

    async def files(_: Context, arguments: dict[str, Any]) -> str:
        given = FilesArguments.model_validate(arguments)
        return render_paths(await running.get().files_page(given), tool_links(FILES.name, given))

    answers: dict[str, Answer] = {
        SEARCH.name: search,
        DEFINITION.name: definition,
        READ.name: read,
        GREP.name: grep,
        FILES.name: files,
    }

    async def list_tools(_: Context, __: PaginatedRequestParams | None) -> ListToolsResult:
        return ListToolsResult(tools=TOOLS)

    async def call_tool(ctx: Context, params: CallToolRequestParams) -> CallToolResult:
        found = answers.get(params.name)
        if found is None:
            raise MCPError(INVALID_PARAMS, f"JendHub has no tool {params.name}.")
        try:
            with answering():
                return answer(await found(ctx, params.arguments or {}))
        except ToolCallError as error:
            return answer(str(error), failed=True)

    return Server(
        "JendHub",
        version=version,
        instructions=INSTRUCTIONS,
        cache_hints={"server/discover": STATIC, "tools/list": STATIC},
        get_tool_input_schema=SCHEMAS.get,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


@dataclass(frozen=True)
class ToolEndpoint:
    manager: StreamableHTTPSessionManager
    guard: Guard

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.guard(Request(scope))
        await self.manager.handle_request(scope, receive, send)


def tool_route(server: Server[Any], guard: Guard) -> Route:
    server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return Route(MCP_PATH, ToolEndpoint(server.session_manager, guard))
