"""A port of the parser of Bend 2 (bendlang/bend, bend2/bend.ts, commit d5fe656).

It keeps Bend's grammar, desugaring, match flattening and name resolution, so
that a file that Bend rejects fails here too, and a name resolves to the same
declaration. It does not check types.
"""

import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

Span = tuple[int, int]

NONE = "None"
LONE = "Lone"
MANY = "Many"

KEYWORDS = frozenset(
    {
        "def", "type", "law", "match", "case", "do", "return",
        "for", "exs", "where", "is", "import",
        "Type", "Data", "Kind", "Quant",
    }
)  # fmt: skip
QUAS = {"0": NONE, "1": LONE, "2": MANY}
ESCAPES = {"n": 10, "t": 9, "r": 13, "0": 0, "\\": 92, "'": 39, '"': 34}
NAT_LITERAL_MAX = 256
INFIX: dict[str, tuple[int, bool, str]] = {
    "<-": (-1, False, ""),
    "->": (0, True, ""),
    "&": (1, True, "Pair"),
    "|": (1, True, "Or"),
    "||": (2, False, "Bool.or"),
    "&&": (3, False, "Bool.and"),
    "<": (4, False, ".is_lt"),
    "<=": (4, False, ".is_le"),
    ">": (4, False, ".is_gt"),
    ">=": (4, False, ".is_ge"),
    "<>": (5, True, "Con"),
    "++": (5, True, "String.append"),
    "<&>": (5, True, ""),
    ".|.": (6, False, ".or"),
    ".^.": (7, False, ".xor"),
    ".&.": (8, False, ".and"),
    "<<": (9, False, ".shln"),
    ">>": (9, False, ".shrn"),
    "+": (10, False, ".add"),
    "-": (10, False, ".sub"),
    "*": (11, False, ".mul"),
    "/": (11, False, ".div"),
    "%": (11, False, ".mod"),
}
NAME = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", re.ASCII)
NUMBER = re.compile(r"(\d+)(n|\.\d+([eE][+-]?\d+)?)?", re.ASCII)
UNICODE_ESCAPE = re.compile(r"u\{([0-9a-fA-F]+)\}")
WORD = re.compile(r"[A-Za-z0-9_.]*")
TYPE_OPERATOR = re.compile(r"(->|[&|](?![&|]))")
NON_SPACE = re.compile(r"\S")


class Kind(StrEnum):
    DEF = "def"
    TYPE = "type"
    LAW = "law"


class ParseError(Exception):
    def __init__(self, expected: str, position: int) -> None:
        super().__init__(expected)
        self.expected = expected
        self.position = position


# Terms


@dataclass(slots=True, eq=False)
class Term:
    s: Span | None = field(default=None, kw_only=True)


@dataclass(slots=True, eq=False)
class Var(Term):
    k: str
    i: int
    v: Term | None = None


@dataclass(slots=True, eq=False)
class Ref(Term):
    k: str


@dataclass(slots=True, eq=False)
class Sub(Term):
    i: int
    v: "Patt"
    f: Term


@dataclass(slots=True, eq=False)
class Let(Term):
    k: list[str]
    i: list[int]
    v: list[Term]
    f: Term
    q: list[str]


@dataclass(slots=True, eq=False)
class Typ(Term):
    g: Term


@dataclass(slots=True, eq=False)
class Qnt(Term):
    pass


@dataclass(slots=True, eq=False)
class Qua(Term):
    q: str


@dataclass(slots=True, eq=False)
class Min(Term):
    a: Term
    b: Term


@dataclass(slots=True, eq=False)
class All(Term):
    q: str
    k: str
    i: int
    A: Term
    B: Term


@dataclass(slots=True, eq=False)
class Lam(Term):
    k: str
    i: int
    f: Term
    q: str | None = None


@dataclass(slots=True, eq=False)
class App(Term):
    f: Term
    x: Term


@dataclass(slots=True, eq=False)
class Adt(Term):
    k: str
    x: list[Term]


@dataclass(slots=True, eq=False)
class Ctr(Term):
    k: str
    x: list[Term]


@dataclass(slots=True, eq=False)
class Lit(Term):
    k: str
    v: int | str


@dataclass(slots=True, eq=False)
class Mat(Term):
    k: str
    h: Term
    m: Term


@dataclass(slots=True, eq=False)
class Efq(Term):
    pass


@dataclass(slots=True, eq=False)
class Eql(Term):
    a: Term
    b: Term
    T: Term


@dataclass(slots=True, eq=False)
class Rfl(Term):
    pass


@dataclass(slots=True, eq=False)
class Rwt(Term):
    e: Term
    p: Term
    f: Term


@dataclass(slots=True, eq=False)
class Hol(Term):
    k: str


@dataclass(slots=True, eq=False)
class Ann(Term):
    x: Term
    T: Term


# Patterns and bodies


@dataclass(slots=True, eq=False)
class PVar:
    k: str
    i: int
    q: str
    s: Span | None = None


@dataclass(slots=True, eq=False)
class PCtr:
    k: str
    x: list["Patt"]
    s: Span | None = None


Patt = PVar | PCtr


@dataclass(slots=True, eq=False)
class Case:
    p: list[Patt]
    f: "Body"


@dataclass(slots=True, eq=False)
class Match:
    e: list[Term]
    r: list[Case]
    s: Span | None = None


@dataclass(slots=True, eq=False)
class Local:
    k: list[Patt]
    q: str
    v: list[Term]
    f: "Body"


Body = Match | Local | Term


# Book


@dataclass(slots=True)
class DefInfo:
    module: str
    x: int
    base: bool
    law: bool
    has_value: bool = False
    foreign: bool = False


@dataclass(slots=True)
class AdtInfo:
    module: str
    n: int
    g: int
    base: bool


@dataclass(slots=True)
class CtrInfo:
    module: str
    n: int
    family: str


@dataclass(frozen=True, slots=True)
class Declaration:
    kind: Kind
    name: str
    key: str
    start: int
    keyword: int
    header_end: int
    end: int
    terms: tuple[Term, ...]
    fills: bool = False


def module_of(key: str) -> str:
    return key.rpartition(":")[0]


@dataclass
class Book:
    tlds: dict[str, DefInfo | AdtInfo] = field(default_factory=dict[str, DefInfo | AdtInfo])
    ctrs: dict[str, CtrInfo] = field(default_factory=dict[str, CtrInfo])
    fills: dict[str, set[str]] = field(default_factory=dict[str, set[str]])


@dataclass
class View:
    book: Book
    closure: frozenset[str]

    def tld(self, key: str) -> DefInfo | AdtInfo | None:
        found = self.book.tlds.get(key)
        return found if found is not None and found.module in self.closure else None

    def ctr(self, key: str) -> CtrInfo | None:
        found = self.book.ctrs.get(key)
        return found if found is not None and found.module in self.closure else None

    def has(self, key: str) -> bool:
        return self.tld(key) is not None or self.ctr(key) is not None

    def filled(self, key: str, info: DefInfo) -> bool:
        if not info.law:
            return info.has_value
        return bool(self.book.fills.get(key, set()) & self.closure)


def char_is_head(c: str) -> bool:
    return c != "" and (("A" <= c <= "Z") or ("a" <= c <= "z") or c == "_")


def char_is_name(c: str) -> bool:
    return char_is_head(c) or ("0" <= c <= "9") or c == "."


def f32_bits(text: str) -> int | None:
    try:
        packed = struct.pack(">f", float(text))
    except OverflowError:
        return None
    return int.from_bytes(packed)


# Parser


class Parser:
    def __init__(self, view: View, text: str, ns: str, aliases: dict[str, str]) -> None:
        self.view = view
        self.str = text
        self.ns = ns
        self.al = aliases
        self.pos = 0
        self.last = 0
        self.stk: list[tuple[str, int]] = []
        self.frs = 0
        self.declarations: list[Declaration] = []

    # Primitives

    def fail(self, expected: str, position: int | None = None) -> ParseError:
        return ParseError(expected, self.pos if position is None else position)

    def col(self, pos: int) -> int:
        return pos - self.str.rfind("\n", 0, pos)

    def span(self, beg: int) -> Span:
        return (beg, self.pos)

    def peek(self) -> str:
        return self.str[self.pos] if self.pos < len(self.str) else ""

    def bump(self) -> str:
        c = self.peek()
        self.pos += 1
        self.last = self.pos
        return c

    def at(self, s: str) -> bool:
        return self.str.startswith(s, self.pos)

    def take(self, s: str) -> bool:
        if not self.at(s):
            return False
        self.pos += len(s)
        self.last = self.pos
        return True

    def skip(self) -> None:
        s = self.str
        n = len(s)
        while self.pos < n:
            c = s[self.pos]
            if c in " \n\r\t":
                self.pos += 1
                continue
            if c == "#":
                newline = s.find("\n", self.pos)
                self.pos = n if newline < 0 else newline
                continue
            return

    def eat(self, s: str) -> None:
        self.skip()
        if not self.take(s):
            raise self.fail(f"'{s}'")

    def at_word(self, w: str) -> bool:
        self.skip()
        if not self.at(w):
            return False
        after = self.pos + len(w)
        return not char_is_name(self.str[after] if after < len(self.str) else "")

    def word(self, w: str) -> bool:
        if not self.at_word(w):
            return False
        self.take(w)
        return True

    def lexeme(self) -> str:
        self.skip()
        if not char_is_head(self.peek()):
            raise self.fail("a name")
        beg = self.pos
        n = len(self.str)
        while self.pos < n and char_is_name(self.str[self.pos]):
            self.pos += 1
        self.last = self.pos
        k = self.str[beg : self.pos]
        if NAME.fullmatch(k) is None:
            raise self.fail(f"a name (words joined by dots, got '{k}')", beg)
        return k

    def name(self) -> str:
        k = self.lexeme()
        if k in KEYWORDS:
            raise self.fail(f"a name (got the keyword '{k}')", self.pos - len(k))
        return k

    def char(self) -> int:
        if self.take("\\"):
            escape = UNICODE_ESCAPE.match(self.str, self.pos, self.pos + 11)
            if escape is not None:
                self.pos = escape.end()
                self.last = self.pos
                return int(escape[1], 16)
            c = ESCAPES.get(self.bump())
            if c is None:
                raise self.fail("an escape")
            return c
        if self.pos >= len(self.str):
            raise self.fail("a character")
        return ord(self.bump())

    def nl(self) -> bool:
        j = self.pos - 1
        while j >= 0:
            c = self.str[j]
            if c == "\n":
                return True
            if c not in " \r\t":
                return False
            j -= 1
        return True

    def more(self, col: int) -> bool:
        return self.at(";") or (self.pos < len(self.str) and self.col(self.pos) == col)

    def fresh_index(self) -> int:
        i = self.frs
        self.frs += 1
        return i

    # Binders

    def open(self, k: str) -> int:
        i = self.fresh_index()
        if k != "_":
            self.stk.append((k, i))
        return i

    def close(self, n: int) -> None:
        del self.stk[n:]

    def var(self, k: str, s: Span | None = None) -> Term:
        for name, index in reversed(self.stk):
            if name == k:
                return Var(k, index, s=s)
        q = self.reso(k)
        if "." in k:
            return Ref(q, s=s)
        return Var(k, self.fresh_index(), Ref(q, s=s), s=s)

    def qual(self, k: str) -> str:
        return k if self.ns == "" else f"{self.ns}:{k}"

    def reso(self, k: str) -> str:
        dot = k.find(".")
        q = self.qual(k)
        if dot != -1 and k[:dot] in self.al:
            q = f"{self.al[k[:dot]]}:{k[dot + 1 :]}"
            if q != k and self.view.has(q) and self.view.has(k):
                raise self.fail(f"an unambiguous name (the alias {k[:dot]} shadows {k})")
        own = self.view.has(q)
        far = self.view.has(k)
        return q if own or not far else k

    def call(self, k: str, xs: list[Term], s: Span | None = None) -> Term:
        out: Term = Ref(self.reso(k), s=s)
        for x in xs:
            out = App(out, x, s=s)
        return out

    def quant(self) -> str:
        self.skip()
        if self.take("-"):
            return NONE
        if self.take("+"):
            return MANY
        return LONE

    # Patterns

    def bind(self, t: Term) -> PVar:
        if not isinstance(t, Var):
            raise self.fail("a lambda binder (one name: k => body)")
        return PVar(t.k, self.open(t.k), MANY if t.i < 0 else LONE, t.s)

    def patt(self, t: Term) -> Patt:
        if isinstance(t, Var):
            if self.view.ctr(self.reso(t.k)) is not None:
                raise self.fail(f"a braced constructor pattern ({t.k} is a constructor)")
            return self.bind(t)
        if isinstance(t, Ctr):
            ctr = self.view.ctr(t.k)
            if ctr is None:
                raise self.fail(f"a declared constructor (unknown: {t.k})")
            if ctr.n != len(t.x):
                raise self.fail(f"a {t.k} pattern with {ctr.n} fields")
            return PCtr(t.k, [self.patt(x) for x in t.x], t.s)
        if isinstance(t, Lit):
            return self.patt(lit_step(t))
        raise self.fail("a pattern (a binder or a constructor)")

    # Terms

    def term(self, lvl: int = 0) -> Term:
        self.skip()
        beg = self.pos
        base = self.term_base(beg)
        if base.s is None:
            base.s = self.span(beg)
        return self.term_ops(base, base.s[0], lvl)

    def term_base(self, beg: int) -> Term:
        c = self.peek()
        if char_is_head(c):
            k = self.lexeme()
            if k == "Type":
                return Typ(Qua(LONE))
            if k == "Data":
                return Typ(Qua(MANY))
            if k == "Quant":
                return Qnt()
            if k == "Kind":
                self.eat("(")
                g = self.term()
                self.eat(")")
                return Typ(g)
            if k == "do":
                m = self.name()
                self.eat("<")
                ts = self.fill(self.reso(m), self.term_args(">"), self.span(beg))
                self.eat(":")
                self.skip()
                return self.do_stmt(m, ts, self.col(self.pos))
            if k == "match":
                raise self.fail("a term (a match heads a def body, not a term)", beg)
            if k == "case":
                raise self.fail("a match heading this case (this case is orphaned)", beg)
            if k == "return":
                raise self.fail("a do-block heading this return", beg)
            if k in KEYWORDS:
                raise self.fail(f"a term (the keyword '{k}' cannot head one)", beg)
            if self.take("{"):
                xs = self.term_args("}")
                return Ctr(self.reso(k), xs)
            return self.var(k, self.span(beg))
        if "0" <= c <= "9":
            return self.term_num()
        if c == "@":
            return self.term_all(False)
        if c == "&":
            q = QUAS.get(self.str[self.pos + 1] if self.pos + 1 < len(self.str) else "")
            if q is not None:
                self.bump()
                self.bump()
                return Qua(q)
            return self.term_all(True)
        if c == "+":
            return self.term_plus(beg)
        if c == "\\":
            return self.term_match(beg)
        if c == "%":
            return self.term_rewrite(beg)
        if c == "{":
            return self.term_brace(beg)
        if c == "(":
            self.bump()
            return self.term_tup(beg)
        if c == "[":
            return self.term_list(beg)
        if c == "'":
            self.bump()
            n = self.char()
            if not self.take("'"):
                raise self.fail("a closing '")
            spn = self.span(beg)
            return Ctr("Chr", [Lit("U32", n, s=spn)], s=spn)
        if c == '"':
            self.bump()
            cs: list[int] = []
            while not self.take('"'):
                if self.pos >= len(self.str):
                    raise self.fail('a closing "')
                cs.append(self.char())
            return lit_of(cs, self.span(beg))
        if c == "?":
            self.bump()
            return Hol(self.name())
        raise self.fail("a term")

    def term_plus(self, beg: int) -> Term:
        self.bump()
        t = self.term(12)
        s = self.span(beg)
        if isinstance(t, Adt):
            k = t.k
        elif isinstance(t, Var | Ref):
            k = self.reso(t.k)
        else:
            k = ""
        tld = self.view.tld(k)
        if isinstance(t, Var) and not isinstance(tld, AdtInfo):
            return Var(t.k, -1, s=s)
        if not isinstance(tld, AdtInfo) or tld.g == 0 or (tld.g < tld.n and not isinstance(t, Adt)):
            raise self.fail(
                "a quantified datatype after + (+D<..> sets D's leading quantities to &2)"
            )
        xs = t.x if isinstance(t, Adt) else [Qua(LONE, s=s) for _ in range(tld.n)]
        return Adt(k, [Qua(MANY, s=s) if i < tld.g else x for i, x in enumerate(xs)], s=s)

    def term_match(self, beg: int) -> Term:
        self.bump()
        self.eat("{")
        arms: list[tuple[str, Term]] = []
        tail: Term = Efq()
        while True:
            self.skip()
            if self.take("}"):
                break
            t = self.term()
            self.skip()
            if isinstance(t, Var | Ref) and self.take(":"):
                h = self.term()
                arms.append((self.reso(t.k), h))
                self.skip()
                self.take(";")
                continue
            tail = t
            self.skip()
            self.take(";")
            self.eat("}")
            break
        s = self.span(beg)
        if tail.s is None:
            tail.s = s
        out = tail
        for k, h in reversed(arms):
            out = Mat(k, h, out, s=s)
        return out

    def term_rewrite(self, beg: int) -> Term:
        self.bump()
        e0 = self.term()
        self.skip()
        k = ""
        e = e0
        if self.take("@"):
            if not isinstance(e0, Var):
                raise self.fail("a name before @ (a rewrite binder is one name: %e@E : P)")
            k = e0.k
            e = self.term()
        self.eat(":")
        n0 = len(self.stk)
        xi = self.fresh_index()
        self.stk.append(("_", xi))
        ei = self.open(k)
        P = self.term()
        self.close(n0)
        self.skip()
        self.take(";")
        f = self.block()
        s = self.span(beg)
        return Rwt(e, Lam("_", xi, Lam(k, ei, P, s=e0.s), s=s), f, s=s)

    def term_brace(self, beg: int) -> Term:
        self.bump()
        self.skip()
        if self.take("=="):
            self.eat("}")
            return Rfl()
        a = self.term()
        self.skip()
        ne = self.take("!=")
        ns = (self.pos - 2, self.pos)
        b = self.term() if ne or self.take("==") else None
        self.eat(":")
        T = self.term()
        self.eat("}")
        if b is None:
            return Ann(a, T)
        if not ne:
            return Eql(a, b, T)
        s = self.span(beg)
        return All(LONE, "_", self.open("_"), Eql(a, b, T, s=s), Ref("Empty", s=ns), s=s)

    def term_list(self, beg: int) -> Term:
        self.bump()
        self.skip()
        xs = [] if self.at("]") else [self.term()]
        self.skip()
        if xs and self.take(":"):
            T = self.term(12)
            self.skip()
            cnt = self.take("*")
            if not cnt:
                self.eat("^")
            n = self.term()
            self.eat("]")
            s = self.span(beg)
            self.term_ns(xs[0], T)
            d = n
            if cnt:
                count = nat_from_term(n) or 0
                if count <= 0 or count & (count - 1):
                    raise self.fail("a power of two count (^d takes a depth)")
                d = Lit("Nat", count.bit_length() - 1, s=n.s)
            return App(App(App(Ref("Array.new", s=s), T, s=s), d, s=s), xs[0], s=s)
        self.take(",")
        ys = xs + self.term_args("]")
        spn = self.span(beg)
        out: Term = Ctr("Nil", [], s=spn)
        for x in reversed(ys):
            out = Ctr("Con", [x, out], s=spn)
        return out

    def term_ops(self, tm: Term, beg: int, lvl: int) -> Term:
        out = tm
        while True:
            self.skip()
            if self.nl() and (self.at("(") or self.at("[")):
                return out
            if self.at("(") or self.at("!("):
                if isinstance(out, Var) and out.v is not None:
                    out = out.v
                if self.at("!"):
                    if not isinstance(out, Ref):
                        raise self.fail("a named def before ! (only f!(..) offloads)")
                    self.bump()
                    continue
                self.bump()
                head = out.k if isinstance(out, Ref) else ""
                hd = self.view.tld(head)
                x = hd.x if isinstance(hd, DefInfo) else 0
                ts: list[Term] = []
                self.skip()
                while x > 0 and self.at("~"):
                    if len(ts) == x:
                        raise self.fail(f"a term ({head} takes {x} ~)")
                    self.bump()
                    ts.append(self.term())
                    self.skip()
                    self.take(",")
                    self.skip()
                xs = ts + self.term_args(")")
                s = self.span(beg)
                for a in xs:
                    out = App(out, a, s=s)
                continue
            if self.at("["):
                self.bump()
                ix = self.term()
                self.eat("]")
                s = self.span(beg)
                self.term_ns(ix, Ref("U32", s=s))
                self.skip()
                if not self.nl() and self.take("<-"):
                    v = self.term(2)
                    out = App(
                        App(
                            App(App(Ref("Array.set", s=s), Ref("U32", s=s), s=s), out, s=s), ix, s=s
                        ),
                        v,
                        s=s,
                    )
                else:
                    out = App(
                        App(App(Ref("Array.get", s=s), Ref("U32", s=s), s=s), out, s=s), ix, s=s
                    )
                continue
            if lvl == 0 and self.take("=>"):
                n0 = len(self.stk)
                x = self.bind(out)
                f = self.block()
                self.close(n0)
                out = Lam(x.k, x.i, f, x.q, s=x.s)
                continue
            op = self.str[self.pos : self.pos + 3]
            while op != "" and op not in INFIX:
                op = op[:-1]
            if op == "":
                return out
            prc, right, k = INFIX[op]
            after = self.pos + len(op)
            nx = self.str[after] if after < len(self.str) else ""
            glued = self.pos > 0 and NON_SPACE.match(self.str[self.pos - 1]) is not None
            if (
                (prc < lvl and not (op == "<" and glued))
                or (op in ("-", "+") and (nx == ">" or char_is_head(nx)))
                or (op[0] == ">" and glued)
                or (op == "%" and not nx.isspace())
            ):
                return out
            self.pos += len(op)
            self.last = self.pos
            t = (self.pos - len(op), self.pos)
            b = self.term(prc if right else prc + 1)
            s = self.span(beg)
            self.skip()
            if op == "<" and glued and TYPE_OPERATOR.match(self.str, self.pos):
                raise self.fail("'>' or ',' (a compound type argument takes parens: F<(A & B)>)")
            if op == "<" and (self.at(">") or self.at(",")):
                if not isinstance(out, Var | Ref):
                    raise self.fail("a family name before <..> (a comparison here needs parens)")
                self.take(",")
                d = self.reso(out.k)
                out = Adt(d, self.fill(d, [b, *self.term_args(">")], s), s=s)
            elif op == "->":
                out = All(LONE, "_", self.open("_"), out, b, s=s)
            elif op == "<&>":
                out = Min(out, b, s=s)
            elif op == "<>":
                out = Ctr(k, [out, b], s=s)
            elif op in ("&", "|"):
                out = App(App(Ref(k, s=s), out, s=s), b, s=s)
            else:
                out = App(App(Ref(k, s=t), out, s=s), b, s=s)

    def term_ns(self, tm: Term, T: Term) -> None:
        if isinstance(tm, Let):
            self.term_ns(tm.f, T)
            return
        f, xs = unapply(tm)
        if not isinstance(f, Ref):
            return
        if f.k.rfind(".") == 0:
            h = unapply(T)[0]
            if not isinstance(h, Var | Ref | Adt):
                raise self.fail("a type name after : (the operators' namespace)")
            f.k = self.reso(h.k + f.k)
        elif f.k not in ("Bool.and", "Bool.or", "String.append"):
            return
        for x in xs:
            self.term_ns(x, T)

    def term_args(self, close: str) -> list[Term]:
        xs: list[Term] = []
        while True:
            self.skip()
            if self.take(close):
                return xs
            xs.append(self.term())
            self.skip()
            self.take(",")

    def term_all(self, exi: bool) -> Term:
        beg = self.pos
        self.bump()
        q = LONE if exi else self.quant()
        k = self.name()
        self.eat(":")
        A = self.term(1)
        self.eat("->")
        n0 = len(self.stk)
        i = self.open(k)
        B = self.term()
        self.close(n0)
        if exi:
            s = self.span(beg)
            return App(App(Ref("Exists", s=s), A, s=s), Lam(k, i, B, s=s), s=s)
        return All(q, k, i, A, B)

    def term_tup(self, beg: int) -> Term:
        self.skip()
        b = self.body(self.col(self.pos) - 1)
        self.skip()
        if not isinstance(b, Match | Local) and self.take(","):
            rest = self.term_tup(beg)
            return Ctr("Tuple", [b, rest], s=self.span(beg))
        out = body_flatten(b, [], self.fresh_index)
        if self.take(":"):
            self.term_ns(out, self.term())
        self.eat(")")
        return out

    def term_num(self) -> Term:
        beg = self.pos
        m = NUMBER.match(self.str, self.pos)
        assert m is not None
        n = int(m[1])
        self.pos = m.end()
        self.last = self.pos
        if m[2] is not None and m[2] != "n":
            bits = f32_bits(m[0])
            if bits is None:
                raise self.fail(f"a float literal with a finite f32 value (got {m[0]})", beg)
            return Lit("F32", bits)
        if m[2] is None:
            if char_is_name(self.peek()):
                raise self.fail("a numeric literal (NUMBER is U32, NUMBER n is Nat)")
            if n > 0xFFFFFFFF:
                raise self.fail(f"a u32 literal up to 4294967295 (got {m[1]})", beg)
            return Lit("U32", n)
        if n > 0xFFFFFFFF:
            raise self.fail(f"a nat literal up to 4294967295n (got {m[1]}n)")
        if self.take("+"):
            out = self.term()
            spn = self.span(beg)
            if isinstance(out, Lit) and out.k == "Nat":
                assert isinstance(out.v, int)
                if n + out.v <= 0xFFFFFFFF:
                    return Lit("Nat", n + out.v, s=spn)
            if n > NAT_LITERAL_MAX:
                return App(App(Ref("Nat.add", s=spn), Lit("Nat", n, s=spn), s=spn), out, s=spn)
            for _ in range(n):
                out = Ctr("Succ", [out], s=spn)
            return out
        if char_is_name(self.peek()):
            raise self.fail("a nat literal (NUMBER n)")
        return Lit("Nat", n)

    def fill(self, k: str, xs: list[Term], s: Span | None = None) -> list[Term]:
        tld = self.view.tld(k)
        if not isinstance(tld, AdtInfo) or len(xs) + tld.g != tld.n:
            return xs
        return [Qua(LONE, s=s) for _ in range(tld.g)] + xs

    def do_stmt(self, m: str, ts: list[Term], col: int) -> Term:
        self.skip()
        beg = self.pos
        if self.word("return"):
            e = self.term()
            return self.call(m + ".pure", [*ts, e], self.span(beg))
        t = self.term()
        self.skip()
        typed = isinstance(t, Var) and self.take(":")
        step = not typed and self.more(col)
        if typed:
            bound = self.term(1)
        elif step:
            bound = self.var("Unit", t.s)
        else:
            bound = t
        self.skip()
        asg = typed and not self.at("==") and self.take("=")
        if typed and not asg:
            self.eat("<-")
        elif not typed and not step and not self.take("<-"):
            k = self.reso(m)
            head = (
                Adt(k, ts, s=t.s)
                if isinstance(self.view.tld(k), AdtInfo)
                else self.call(m, ts, t.s)
            )
            return Ann(t, head, s=t.s)
        v = t if step else self.term()
        self.skip()
        self.take(";")
        s = self.span(beg)
        n0 = len(self.stk)
        x = self.bind(t if typed else Var("_", 0))
        f = self.do_stmt(m, ts, col)
        self.close(n0)
        if asg:
            return Let([x.k], [x.i], [Ann(v, bound, s=s)], f, [x.q], s=s)
        return self.call(m + ".bind", [*ts[:-1], bound, *ts[-1:], v, Lam(x.k, x.i, f, x.q, s=s)], s)

    # Bodies

    def body(self, col: int = 0) -> Body:
        self.skip()
        beg = self.pos
        if self.word("match"):
            es = self.terms()
            self.skip()
            ccol = self.col(self.pos)
            rows: list[Case] = []
            while ccol > col and self.at_word("case") and self.col(self.pos) >= ccol:
                rcol = self.col(self.pos)
                self.word("case")
                self.skip()
                qbeg = self.pos
                qs = self.terms()
                if len(qs) != len(es):
                    raise self.fail(f"{len(es)} patterns (one per scrutinee)", qbeg)
                n0 = len(self.stk)
                pp = [self.patt(q) for q in qs]
                f = self.body(rcol)
                self.close(n0)
                rows.append(Case(pp, f))
            return Match(es, rows, self.span(beg))
        q = NONE if self.take("-") else LONE
        vs: list[Term] = []
        ts: list[Term] = [Var(self.name(), 0, s=self.span(beg)) if q == NONE else self.term()]
        self.skip()
        while (
            not self.nl()
            and (char_is_head(self.peek()) or (q == LONE and self.at("+")))
            and (WORD.match(self.str, self.pos) or [""])[0] not in KEYWORDS
        ):
            ts.append(self.term())
            self.skip()
        at, at_last = self.pos, self.last
        typed = self.term() if len(ts) == 1 and self.take(":") else None
        self.skip()
        if typed is not None and not self.at("="):
            self.pos, self.last, typed = at, at_last, None
        if (
            typed is None
            and q == LONE
            and len(ts) == 1
            and not (self.at("=") and not self.at("=="))
        ):
            w = term_write(ts[0], beg)
            if w is None or not self.more(self.col(beg)):
                return ts[0]
            vs.append(ts[0])
            ts = [w]
        else:
            self.eat("=")
            if typed is not None:
                vs.append(Ann(self.term(), typed, s=self.span(beg)))
        while len(vs) < len(ts):
            vs.append(self.term())
        self.skip()
        self.take(";")
        n0 = len(self.stk)
        ks: list[Patt] = []
        for x in ts:
            if (len(ts) > 1 or typed is not None) and not isinstance(x, Var):
                raise self.fail("a name (a parallel or typed let binds names)")
            ks.append(self.patt(x))
        f = self.body(col)
        self.close(n0)
        return Local(ks, q, vs, f)

    def block(self) -> Term:
        self.skip()
        b = self.body(self.col(self.pos) - 1)
        return body_flatten(b, [], self.fresh_index)

    def terms(self) -> list[Term]:
        xs: list[Term] = []
        while True:
            xs.append(self.term())
            self.skip()
            if self.take(":"):
                return xs
            self.take(",")

    # Telescopes

    def tele(
        self, close: str, templates: list[str] | None = None
    ) -> list[tuple[str, str, int, Term, Span]]:
        tk = [] if templates is None else templates
        cells: list[tuple[str, str, int, Term, Span]] = []
        while True:
            self.skip()
            if self.take(close):
                return cells
            if close == ")" and self.at("~") and len(tk) < len(cells):
                raise self.fail("a plain binder (only leading binders take ~)")
            ct = close == ")" and self.take("~")
            q = NONE if ct else self.quant()
            beg = self.pos
            k = self.name()
            if close == "}" and any(cell[1] == k for cell in cells):
                raise self.fail(
                    f"a fresh field name (duplicate declaration: {k})", self.pos - len(k)
                )
            s = self.span(beg)
            self.skip()
            bare = q == LONE and not self.at(":")
            if not bare:
                self.eat(":")
            T: Term = Qnt(s=s) if bare else self.term()
            if ct:
                tk.append(k)
            cells.append((NONE if bare else q, k, self.open(k), T, s))
            self.skip()
            self.take(",")

    # Declarations

    def fresh(self, nm: str, ctrs: bool = False, what: str = "a fresh name") -> str:
        k = self.qual(nm)
        a = nm[: nm.find(".")] if "." in nm else ""
        found = self.view.ctr if ctrs else self.view.tld
        if found(k) is not None or found(nm) is not None or a in self.al:
            reason = f"{a} is an import's alias" if a in self.al else f"duplicate declaration: {nm}"
            raise self.fail(f"{what} ({reason})", self.pos - len(nm))
        return k

    def declare(self, k: str, info: DefInfo | AdtInfo) -> None:
        self.view.book.tlds[k] = info

    def parse_def(self, start: int, unsafe: bool) -> None:
        keyword = self.pos - 3
        nm = self.name()
        q = self.reso(nm)
        tld = self.view.tld(q)
        law = (
            q
            if isinstance(tld, DefInfo)
            and not tld.base
            and not tld.foreign
            and not self.view.filled(q, tld)
            else None
        )
        k = q if law is not None else self.fresh(nm)
        self.take("?")
        self.eat("(")
        self.skip()
        if law is not None and self.at("~"):
            raise self.fail("a name")
        templates: list[str] = []
        tele = self.tele(")", templates)
        self.skip()
        terms: list[Term] = []
        if law is not None:
            assert isinstance(tld, DefInfo)
            if any(not isinstance(cell[3], Qnt) for cell in tele):
                raise self.fail("a name")
            if len(tele) < tld.x:
                raise self.fail(f"a name for each ~ clause of the law ({tld.x})")
            info = tld
            header_end = -1
        else:
            if not self.take("->"):
                raise self.fail(
                    f"'->' (a def with no return type fills a law; no law named {nm} is in scope)"
                )
            returned = self.term()
            header_end = self.last
            T = tele_bind(tele, returned)
            info = DefInfo(module=self.ns, x=len(templates), base=False, law=False)
            self.declare(k, info)
            terms.append(T)
        self.eat(":")
        if self.at_word("import"):
            if info.x > 0:
                raise self.fail("a body (a template is not foreign)")
            info.foreign = True
            while self.word("import"):
                self.eat('"')
                effect = ""
                while self.peek() not in ('"', ""):
                    effect += self.bump()
                self.eat('"')
                if re.search(r"\.(c|js)$", effect) is None:
                    raise self.fail("a .c or .js path")
        else:
            params = [PVar(cell[1], cell[2], LONE, cell[4]) for cell in tele]
            terms.append(body_flatten(self.body(), params, self.fresh_index))
            if law is not None:
                self.view.book.fills.setdefault(k, set()).add(self.ns)
            else:
                info.has_value = True
        self.declarations.append(
            Declaration(
                Kind.DEF,
                nm,
                k,
                start,
                keyword,
                header_end,
                self.last,
                tuple(terms),
                law is not None,
            )
        )

    def parse_type(self, start: int) -> None:
        keyword = self.pos - 4
        nm = self.name()
        k = self.fresh(nm)
        self.skip()
        params = self.tele(">") if self.take("<") else []
        if not self.word("is"):
            raise self.fail("'is'")
        K = self.term()
        self.eat(":")
        g = next((n for n, cell in enumerate(params) if not isinstance(cell[3], Qnt)), -1)
        self.declare(
            k, AdtInfo(module=self.ns, n=len(params), g=len(params) if g < 0 else g, base=False)
        )
        terms: list[Term] = [tele_bind(params, K)]
        while True:
            self.skip()
            if not char_is_head(self.peek()) or any(
                self.at_word(w) for w in ("def", "type", "law")
            ):
                break
            c = self.fresh(self.name(), ctrs=True, what="a fresh constructor name")
            self.eat("{")
            n1 = len(self.stk)
            fields = self.tele("}")
            tip = Adt(k, [Var(cell[1], cell[2]) for cell in params])
            terms.append(tele_bind(params + fields, tip))
            self.close(n1)
            self.view.book.ctrs[c] = CtrInfo(module=self.ns, n=len(fields), family=k)
        self.declarations.append(
            Declaration(Kind.TYPE, nm, k, start, keyword, -1, self.last, tuple(terms))
        )

    def parse_law(self, start: int) -> None:
        keyword = self.pos - 3
        nm = self.name()
        k = self.fresh(nm)
        self.eat(":")
        clauses: list[tuple[bool, str, str, int, Term, Span]] = []
        tc = 0
        while self.at_word("for") or self.at_word("exs"):
            every = self.word("for")
            if not every:
                self.word("exs")
            self.skip()
            if every and self.at("~") and tc < len(clauses):
                raise self.fail("a plain clause (only leading clauses take ~)")
            ct = every and self.take("~")
            q = NONE if ct else (self.quant() if every else LONE)
            beg = self.pos
            c = self.name()
            s = self.span(beg)
            if ct:
                tc += 1
            self.eat(":")
            domain = self.term()
            if self.at_word("where"):
                wbeg = self.pos
                self.word("where")
                ws = self.span(wbeg)
                n1 = len(self.stk)
                i = self.open(c)
                w = self.term()
                self.close(n1)
                domain = App(App(Ref("Exists", s=ws), domain, s=s), Lam(c, i, w, s=s), s=s)
            clauses.append((every, q, c, self.open(c), domain, s))
        statement = self.block()
        for every, q, c, i, domain, s in reversed(clauses):
            if every:
                statement = All(q, c, i, domain, statement, s=s)
            else:
                exists = App(Ref("Exists", s=s), domain, s=s)
                statement = App(exists, Lam(c, i, statement, s=s), s=s)
        self.declare(k, DefInfo(module=self.ns, x=tc, base=False, law=True))
        self.declarations.append(
            Declaration(Kind.LAW, nm, k, start, keyword, -1, self.last, (statement,))
        )

    def book(self) -> list[Declaration]:
        while True:
            self.skip()
            if self.pos >= len(self.str):
                return self.declarations
            self.frs = 0
            self.close(0)
            start = self.pos
            if self.take("@"):
                if not self.word("unsafe"):
                    raise self.fail("'unsafe' (the one decorator)")
                if not self.word("def"):
                    raise self.fail("'def' (@unsafe marks the def below it)")
                self.parse_def(start, True)
                continue
            if self.word("def"):
                self.parse_def(start, False)
                continue
            if self.word("type"):
                self.parse_type(start)
                continue
            if self.word("law"):
                self.parse_law(start)
                continue
            raise self.fail("'def', 'type' or 'law'")


# Helpers


def unapply(tm: Term) -> tuple[Term, list[Term]]:
    xs: list[Term] = []
    cur = tm
    while isinstance(cur, App):
        xs.append(cur.x)
        cur = cur.f
    xs.reverse()
    return cur, xs


def term_write(t: Term, beg: int) -> Term | None:
    h, xs = unapply(t)
    if (
        isinstance(h, Ref)
        and h.k == "Array.set"
        and len(xs) == 4
        and isinstance(xs[1], Var)
        and xs[1].s is not None
        and xs[1].s[0] == beg
    ):
        return xs[1]
    return None


def tele_bind(tele: list[tuple[str, str, int, Term, Span]], end: Term) -> Term:
    out = end
    for q, k, i, T, s in reversed(tele):
        out = All(q, k, i, T, out, s=s)
    return out


def lit_of(cs: list[int], s: Span) -> Term:
    if all(c <= 0x10FFFF and not (0xD800 <= c <= 0xDFFF) for c in cs):
        return Lit("String", "".join(map(chr, cs)), s=s)
    out: Term = Ctr("SNil", [], s=s)
    for c in reversed(cs):
        out = Ctr("SCon", [Ctr("Chr", [Lit("U32", c, s=s)], s=s), out], s=s)
    return out


def word_to_term(n: int, s: Span | None) -> Term:
    out: Term = Ctr("WNil", [], s=s)
    for i in range(31, -1, -1):
        bit = Ctr("True" if (n >> i) & 1 else "False", [], s=s)
        out = Ctr("WCon", [bit, out], s=s)
    return out


def lit_step(t: Lit) -> Term:
    s = t.s
    if t.k == "String":
        assert isinstance(t.v, str)
        if t.v == "":
            return Ctr("SNil", [], s=s)
        head = Ctr("Chr", [Lit("U32", ord(t.v[0]), s=s)], s=s)
        return Ctr("SCon", [head, Lit("String", t.v[1:], s=s)], s=s)
    assert isinstance(t.v, int)
    if t.k != "Nat":
        return Ctr(t.k, [word_to_term(t.v, s)], s=s)
    if t.v == 0:
        return Ctr("Zero", [], s=s)
    return Ctr("Succ", [Lit("Nat", t.v - 1, s=s)], s=s)


def nat_from_term(t: Term) -> int | None:
    n = 0
    while isinstance(t, Ctr) and t.k == "Succ" and len(t.x) == 1:
        n += 1
        t = t.x[0]
    if isinstance(t, Ctr) and t.k == "Zero" and not t.x:
        return n
    if isinstance(t, Lit) and t.k == "Nat" and isinstance(t.v, int) and n + t.v <= 0xFFFFFFFF:
        return n + t.v
    return None


# Flatten


def quant_join(a: str, b: str) -> str:
    if MANY in (a, b):
        return MANY
    return b if a == NONE else a


def quant_dem(q: str, qt: str) -> str:
    return NONE if q == NONE else qt


def patt_term(q: Patt, s: Span | None = None) -> Term:
    if isinstance(q, PVar):
        return Var(q.k, q.i, s=s or q.s)
    return Ctr(q.k, [patt_term(x, s) for x in q.x], s=s or q.s)


def patt_mark(x: PVar, rows: list[Case]) -> str:
    q = x.q
    for row in rows:
        p = row.p[0]
        if isinstance(p, PVar):
            q = quant_join(q, p.q)
    return q


def body_sub(b: Body, i: int, v: Patt) -> Body:
    def scrut(e: Term) -> Term:
        if isinstance(e, Var):
            return e if e.i != i else patt_term(v, e.s)
        return Sub(i, v, e)

    if isinstance(b, Match):
        return Match(
            [scrut(e) for e in b.e], [Case(row.p, body_sub(row.f, i, v)) for row in b.r], b.s
        )
    if isinstance(b, Local):
        return Local(b.k, b.q, [scrut(e) for e in b.v], body_sub(b.f, i, v))
    return Sub(i, v, b)


def match_flatten(m: Match, vars: list[PVar], fr: Callable[[], int]) -> Term:
    if not m.e and m.r:
        return body_flatten(m.r[0].f, vars, fr)
    if not m.e:
        raise ParseError("a case (this match has no row to return)", m.s[0] if m.s else 0)
    if not vars:
        e = m.e[0]
        while isinstance(e, Sub):
            e = e.f
        position = e.s[0] if e.s else (m.s[0] if m.s else 0)
        raise ParseError("a matchable scrutinee", position)
    x = vars[0]
    scu = m.e[0]
    c = next((row.p[0] for row in m.r if isinstance(row.p[0], PCtr)), None)
    v = next((w for w in vars if isinstance(scu, Var) and w.i == scu.i), None)
    if v is not None and c is None and m.r:
        w = PVar(v.k, v.i, patt_mark(v, m.r), v.s)
        rows: list[Case] = []
        for row in m.r:
            p0 = row.p[0]
            if not isinstance(p0, PVar):
                raise ParseError("a variable pattern", p0.s[0] if p0.s else 0)
            rows.append(Case(row.p[1:], body_sub(row.f, p0.i, w)))
        return match_flatten(Match(m.e[1:], rows, m.s), [w if u is v else u for u in vars], fr)
    if v is x:
        if c is None:
            return Efq(s=m.s)
        assert isinstance(c, PCtr)
        xq = patt_mark(x, m.r)
        xs: list[PVar] = []
        for q in c.x:
            if isinstance(q, PVar):
                xs.append(PVar(q.k, q.i, quant_join(q.q, xq), q.s))
            else:
                i = fr()
                xs.append(PVar(f"_{i}", i, xq, q.s))
        kx = PCtr(c.k, list(xs), x.s)
        ps: list[Case] = []
        for row in m.r:
            p0 = row.p[0]
            if isinstance(p0, PCtr):
                if p0.k != c.k:
                    continue
                ps.append(Case([*p0.x, *row.p[1:]], body_sub(row.f, x.i, kx)))
            else:
                g = body_sub(row.f, p0.i, x)
                ps.append(Case([*xs, *row.p[1:]], body_sub(g, x.i, kx)))
        pe = [patt_term(q) for q in xs] + m.e[1:]
        pt = match_flatten(Match(pe, ps, m.s), [*xs, *vars[1:]], fr)
        ds = [row for row in m.r if not isinstance(row.p[0], PCtr) or row.p[0].k != c.k]
        dt = match_flatten(Match(m.e, ds, m.s), vars, fr)
        return Mat(c.k, pt, dt, s=c.s)
    t = match_flatten(m, vars[1:], fr)
    return Lam(x.k, x.i, t, x.q, s=x.s)


def body_flatten(b: Body, vars: list[PVar], fr: Callable[[], int]) -> Term:
    if isinstance(b, Local):
        if len(b.k) == 1 and isinstance(b.k[0], PCtr):
            first = b.v[0]
            return match_flatten(Match([first], [Case([b.k[0]], b.f)], first.s), vars, fr)
        ws = [w for w in b.k if isinstance(w, PVar)]
        g = body_flatten(b.f, ws, fr)
        for w in ws:
            if not isinstance(g, Lam):
                raise ParseError("a parameter or field scrutinee", w.s[0] if w.s else 0)
            g = g.f
        x = Let(
            [w.k for w in ws],
            [w.i for w in ws],
            b.v,
            g,
            [quant_dem(b.q, w.q) for w in ws],
            s=ws[0].s,
        )
        return body_flatten(x, vars, fr)
    if isinstance(b, Match):
        return match_flatten(b, vars, fr)
    if not vars:
        return b
    v = vars[0]
    return Lam(v.k, v.i, body_flatten(b, vars[1:], fr), v.q, s=v.s)
