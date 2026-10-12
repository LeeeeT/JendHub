from jendhub.definitions import BASE, Definition, Origin, SourceFile, extract
from jendhub.hub import Release

PAIR = Origin(Release("kit", "0.1.0.0"), "pair")

SOURCE = """\
import Base
import kit@0.1.0.0/laws.bend as Laws

# Pairs
# =====

# Two naturals.
type Pair is Data:
  Pair{fst: Nat, snd: Nat}

# Swaps the two naturals.
def Pair.swap(
  p: Pair,
) -> Pair:  # noqa: a trailing comment
  def_inner = p
  def_inner
# A section note.

@unsafe def loop(n: Nat) -> Nat:
  loop(n)

law swap_twice:
for p: Pair
  {Pair.swap(Pair.swap(p)) == p : Pair}

def swap_twice(p):
  {==}

def Laws.sorted(xs):
  {==}
"""

PAIR_TEXT = "# Two naturals.\ntype Pair is Data:\n  Pair{fst: Nat, snd: Nat}"
SWAP_HEAD = (
    "# Swaps the two naturals.\ndef Pair.swap(\n  p: Pair,\n) -> Pair:  # noqa: a trailing comment"
)
SWAP_TEXT = f"{SWAP_HEAD}\n  def_inner = p\n  def_inner"
LOOP_TEXT = "@unsafe def loop(n: Nat) -> Nat:\n  loop(n)"
LAW_TEXT = "law swap_twice:\nfor p: Pair\n  {Pair.swap(Pair.swap(p)) == p : Pair}"


def test_extract_reads_each_top_level_definition() -> None:
    assert extract(SourceFile(PAIR, SOURCE)) == [
        Definition("kit@0.1.0.0/pair:Pair", "type", "Pair", PAIR, PAIR_TEXT, PAIR_TEXT),
        Definition("kit@0.1.0.0/pair:Pair.swap", "def", "Pair.swap", PAIR, SWAP_TEXT, SWAP_HEAD),
        Definition(
            "kit@0.1.0.0/pair:loop",
            "def",
            "loop",
            PAIR,
            LOOP_TEXT,
            "@unsafe def loop(n: Nat) -> Nat:",
        ),
        Definition("kit@0.1.0.0/pair:swap_twice", "law", "swap_twice", PAIR, LAW_TEXT, LAW_TEXT),
    ]


def test_extract_names_base_definitions_without_a_prefix() -> None:
    [definition] = extract(SourceFile(BASE, "def Nat.id(n: Nat) -> Nat:\n  n\n"))
    assert definition.id == "Nat.id"


def test_the_head_of_a_def_ends_at_the_colon_after_balanced_parentheses() -> None:
    source = "def f(g: (Nat) -> Nat,\n  x: Nat) -> {f(x) == x : Nat}:\n  {==}\n"
    [definition] = extract(SourceFile(BASE, source))
    assert definition.head == "def f(g: (Nat) -> Nat,\n  x: Nat) -> {f(x) == x : Nat}:"
