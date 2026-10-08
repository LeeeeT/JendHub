"""Loads Bend files as Bend 2's book_load does, from the files of the mirror."""

import posixpath
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field

from jend.parser import (
    Adt,
    All,
    Ann,
    App,
    Book,
    Ctr,
    Declaration,
    Eql,
    Kind,
    Lam,
    Let,
    Mat,
    Min,
    ParseError,
    Parser,
    Patt,
    PCtr,
    Ref,
    Rwt,
    Sub,
    Term,
    Typ,
    Var,
    View,
)

IMPORT = re.compile(r"import\s+(\S+)(?:\s+as\s+([A-Za-z_]\w*))?\s*(?:#.*)?", re.ASCII)
IMPORT_LINE = re.compile(r"import(\s|$)")
NAMED = re.compile(
    r"([a-z][a-z0-9-]{0,63})@((?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){3})", re.ASCII
)
HUB = re.compile(r"0x[0-9a-f]+/")
PLAIN_PATH = re.compile(r"(/|(\.\./)*)([A-Za-z_][\w-]*/)*[A-Za-z_][\w-]*", re.ASCII)
BASE = ""
JS_WHITESPACE = "\t\n\v\f\r                  　﻿"
RECURSION_LIMIT = 200_000

FileKey = tuple[str, str]


class LoadError(Exception):
    pass


@dataclass(frozen=True)
class Import:
    target: str
    alias: str
    module: str


@dataclass(frozen=True)
class ImportLine:
    index: int
    target: str
    alias: str | None


def import_lines(text: str) -> list[ImportLine]:
    found: list[ImportLine] = []
    for index, line in enumerate(text.split("\n")):
        stripped = line.strip(JS_WHITESPACE)
        if stripped == "" or stripped.startswith("#"):
            continue
        if IMPORT_LINE.match(stripped) is None:
            break
        match = IMPORT.fullmatch(stripped)
        if match is None or (match[2] is None and match[1] != "Base"):
            raise LoadError(f"an import ('import Base', or 'import <path> as <Name>'): {stripped}")
        found.append(ImportLine(index, match[1], match[2]))
    return found


@dataclass(frozen=True)
class Definition:
    kind: Kind
    name: str
    key: str
    signature: str
    doc: str
    line: int
    first_line: int
    last_line: int
    refs: tuple[str, ...]


@dataclass(frozen=True)
class Module:
    ns: str
    imports: tuple[Import, ...]
    closure: frozenset[str]
    text: str
    declarations: tuple[Declaration, ...]


@dataclass
class Library:
    sources: dict[FileKey, str]
    names: dict[str, str]
    base: FileKey

    def text(self, key: FileKey) -> str | None:
        return self.sources.get(key)


@dataclass
class Loader:
    library: Library
    book: Book = field(default_factory=Book)
    modules: dict[FileKey, Module] = field(default_factory=dict[FileKey, Module])
    errors: dict[FileKey, str] = field(default_factory=dict[FileKey, str])
    loading: set[FileKey] = field(default_factory=set[FileKey])

    def load(self, key: FileKey) -> Module:
        if key in self.modules:
            return self.modules[key]
        if key in self.errors:
            raise LoadError(self.errors[key])
        if key in self.loading:
            raise LoadError(f"an import cycle through {key[1]}")
        self.loading.add(key)
        try:
            module = self._load(key)
        except (LoadError, ParseError, RecursionError) as error:
            self.errors[key] = _reason(error)
            raise LoadError(self.errors[key]) from error
        finally:
            self.loading.discard(key)
        self.modules[key] = module
        return module

    def _load(self, key: FileKey) -> Module:
        text = self.library.text(key)
        if text is None:
            raise LoadError(f"no such file: {key[0]}/{key[1]}")
        is_base = key == self.library.base
        ns = BASE if is_base else f"{key[0]}/{key[1].removesuffix('.bend')}"
        body = text.split("\n")
        aliases: dict[str, str] = {}
        imports: list[Import] = []
        closure = {ns}
        for line in import_lines(text):
            body[line.index] = ""
            if line.alias is None:
                closure |= self.load(self.library.base).closure
                continue
            target, alias = line.target, line.alias
            if not target.endswith(".bend"):
                raise LoadError(f"an import of a .bend file: {target}")
            if alias in aliases:
                raise LoadError(f"a fresh alias ({alias} names an earlier import)")
            dependency = self._resolve(key, target)
            module = self.load(dependency)
            aliases[alias] = module.ns
            imports.append(Import(target, alias, module.ns))
            closure |= module.closure
        parse_text = "\n".join(body)
        visible = frozenset(closure)
        declarations = Parser(View(self.book, visible), parse_text, ns, aliases).book()
        for declaration in declarations:
            for term in declaration.terms:
                _check_operators(term)
        if is_base:
            for info in self.book.tlds.values():
                if info.module == BASE:
                    info.base = True
        return Module(ns, tuple(imports), visible, parse_text, tuple(declarations))

    def _resolve(self, importer: FileKey, target: str) -> FileKey:
        named = re.match(r"([^/]*@[^/]*)/", target)
        spelled = target
        if named is not None:
            if NAMED.fullmatch(named[1]) is None:
                raise LoadError(f"a package as <name>@<version>: {named[1]}")
            package = self.library.names.get(named[1])
            if package is None:
                raise LoadError(f"a package named {named[1]}")
            spelled = package + target[len(named[1]) :]
        hub = HUB.match(spelled) is not None
        if not _plain(spelled.removeprefix("./")[:-5]):
            raise LoadError(f"an import path of plain names: {target}")
        if hub:
            resolved = posixpath.normpath(spelled)
        else:
            folder = posixpath.dirname(f"{importer[0]}/{importer[1]}")
            resolved = posixpath.normpath(posixpath.join(folder, spelled))
        package, _, path = resolved.partition("/")
        if HUB.match(resolved) is None or not _plain(resolved.removesuffix(".bend")):
            raise LoadError(f"an import path of plain names: {target}")
        return package, path

    def uses(self, declaration: Declaration) -> set[str]:
        found: set[str] = set()
        for name in _names(declaration.terms):
            ctr = self.book.ctrs.get(name)
            target = name if ctr is None else ctr.family
            if target != declaration.key and target in self.book.tlds:
                found.add(target)
        return found

    def references(self) -> dict[str, set[str]]:
        refs: dict[str, set[str]] = {}
        for module in self.modules.values():
            for declaration in module.declarations:
                refs.setdefault(declaration.key, set()).update(self.uses(declaration))
        return refs

    def definitions(self, key: FileKey, refs: dict[str, set[str]]) -> tuple[Definition, ...]:
        module = self.modules[key]
        lines = module.text.split("\n")
        return tuple(
            self._definition(module, lines, declaration, refs)
            for declaration in module.declarations
            if not declaration.fills
        )

    def _definition(
        self, module: Module, lines: list[str], declaration: Declaration, refs: dict[str, set[str]]
    ) -> Definition:
        text = module.text
        start_line = text.count("\n", 0, declaration.start)
        keyword_line = text.count("\n", 0, declaration.keyword)
        end_line = text.count("\n", 0, declaration.end)
        doc_lines: list[str] = []
        index = start_line - 1
        while index >= 0 and lines[index].startswith("#"):
            doc_lines.append(lines[index].lstrip("#").strip())
            index -= 1
        if declaration.kind is Kind.DEF:
            signature = _collapse(_strip_comments(text[declaration.start : declaration.header_end]))
        else:
            block = text[declaration.start : declaration.end].split("\n")
            signature = "\n".join(line.rstrip() for line in block if line.strip())
        return Definition(
            kind=declaration.kind,
            name=declaration.name,
            key=declaration.key,
            signature=signature,
            doc=" ".join(reversed(doc_lines)),
            line=keyword_line + 1,
            first_line=start_line - len(doc_lines),
            last_line=end_line,
            refs=tuple(sorted(refs.get(declaration.key, ()))),
        )


def load_all(library: Library, keys: list[FileKey]) -> Loader:
    loader = Loader(library)
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, RECURSION_LIMIT))
    try:
        for key in keys:
            try:
                loader.load(key)
            except LoadError:
                pass
    finally:
        sys.setrecursionlimit(limit)
    return loader


def _reason(error: Exception) -> str:
    if isinstance(error, RecursionError):
        return "too deeply nested to parse"
    if isinstance(error, ParseError):
        return f"expected {error.expected} at offset {error.position}"
    return str(error)


def _plain(path: str) -> bool:
    return PLAIN_PATH.fullmatch(HUB.sub("", path, count=1)) is not None


def _check_operators(term: Term) -> None:
    for node in _walk((term,)):
        if isinstance(node, Ref) and node.k.rfind(".") == 0:
            raise LoadError(f"a type for the operator {node.k[1:]} (write (a op b : T))")


def _names(terms: tuple[Term, ...]) -> Iterator[str]:
    for node in _walk(terms):
        if isinstance(node, Ref | Adt | Ctr | Mat):
            yield node.k
        elif isinstance(node, Sub):
            yield from _pattern_names(node.v)


def _pattern_names(pattern: Patt) -> Iterator[str]:
    stack = [pattern]
    while stack:
        current = stack.pop()
        if isinstance(current, PCtr):
            yield current.k
            stack.extend(current.x)


def _walk(terms: tuple[Term, ...]) -> Iterator[Term]:
    stack: list[Term] = list(terms)
    while stack:
        node = stack.pop()
        yield node
        match node:
            case Var(v=v) if v is not None:
                stack.append(v)
            case Sub(f=f) | Lam(f=f):
                stack.append(f)
            case Let(v=v, f=f):
                stack.extend(v)
                stack.append(f)
            case Typ(g=g):
                stack.append(g)
            case Min(a=a, b=b):
                stack.extend((a, b))
            case All(A=A, B=B):
                stack.extend((A, B))
            case App(f=f, x=x):
                stack.extend((f, x))
            case Adt(x=x) | Ctr(x=x):
                stack.extend(x)
            case Mat(h=h, m=m):
                stack.extend((h, m))
            case Eql(a=a, b=b, T=annotation):
                stack.extend((a, b, annotation))
            case Rwt(e=e, p=p, f=f):
                stack.extend((e, p, f))
            case Ann(x=x, T=annotation):
                stack.extend((x, annotation))
            case _:
                pass


def _strip_comments(text: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "'\"":
            end = index + 1
            while end < len(text) and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            out.append(text[index : end + 1])
            index = end + 1
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t\n"):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _collapse(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([(\[{])\s", r"\1", text)
    return re.sub(r"\s([)\]}])", r"\1", text).strip()
