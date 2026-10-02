from jend.signatures import Definition, Kind, extract

SOURCE = """\
import Base

# Wraps a value.
# Second line.
@unsafe
def wrap(
  -A: Data, x: A
) -> Maybe<&2, A>:
  Some{x}

def quoted(+c: Char) -> {f('[', c) == c : Char}:
  c

def dependent(n: Nat) -> @x: Nat -> Vec(x):
  go(n)

def inline(a: U32, b: U32) -> U32: U32.add(a, b)

def Laws.wrap_ok(x):
  {==}

type Pair<-A: Data> is Data:
  # the two halves
  Pair{fst: A, snd: A}

# A law.
law wrap_ok:
  for x: U32
  {wrap(U32, x) == Some{x} : Maybe<&2, U32>}

# Doc of nothing.
"""


def test_extract_reads_every_declaration_kind() -> None:
    extraction = extract(SOURCE)

    assert extraction.unparsed_lines == ()
    assert extraction.definitions == (
        Definition(
            Kind.DEF,
            "wrap",
            "def wrap(-A: Data, x: A) -> Maybe<&2, A>",
            "Wraps a value. Second line.",
            6,
        ),
        Definition(Kind.DEF, "quoted", "def quoted(+c: Char) -> {f('[', c) == c : Char}", "", 11),
        Definition(Kind.DEF, "dependent", "def dependent(n: Nat) -> @x: Nat -> Vec(x)", "", 14),
        Definition(Kind.DEF, "inline", "def inline(a: U32, b: U32) -> U32", "", 17),
        Definition(
            Kind.TYPE,
            "Pair",
            "type Pair<-A: Data> is Data:\n  # the two halves\n  Pair{fst: A, snd: A}",
            "",
            22,
        ),
        Definition(
            Kind.LAW,
            "wrap_ok",
            "law wrap_ok:\n  for x: U32\n  {wrap(U32, x) == Some{x} : Maybe<&2, U32>}",
            "A law.",
            27,
        ),
    )


def test_extract_reports_a_header_without_end() -> None:
    extraction = extract("def broken(x: U32\n")

    assert extraction.definitions == ()
    assert extraction.unparsed_lines == (1,)
