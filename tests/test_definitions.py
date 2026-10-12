from jendhub.definitions import Definition, SourceFile, extract

SOURCE = """\
import Base
import kit@0.1.0.0/laws.bend as Laws

# Pairs
# =====

# Two naturals.
type Pair is Data:
  Pair{fst: Nat, snd: Nat}

def Pair.swap(
  p: Pair,
) -> Pair:
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


def test_extract_reads_each_top_level_definition() -> None:
    assert extract(SourceFile("kit@0.1.0.0/pair:", SOURCE)) == [
        Definition(
            "kit@0.1.0.0/pair:Pair",
            "type",
            "Pair",
            "# Two naturals.\ntype Pair is Data:\n  Pair{fst: Nat, snd: Nat}",
        ),
        Definition(
            "kit@0.1.0.0/pair:Pair.swap",
            "def",
            "Pair.swap",
            "def Pair.swap(\n  p: Pair,\n) -> Pair:\n  def_inner = p\n  def_inner",
        ),
        Definition(
            "kit@0.1.0.0/pair:loop",
            "def",
            "loop",
            "@unsafe def loop(n: Nat) -> Nat:\n  loop(n)",
        ),
        Definition(
            "kit@0.1.0.0/pair:swap_twice",
            "law",
            "swap_twice",
            "law swap_twice:\nfor p: Pair\n  {Pair.swap(Pair.swap(p)) == p : Pair}",
        ),
    ]


def test_extract_names_base_definitions_without_a_prefix() -> None:
    [definition] = extract(SourceFile("", "def Nat.id(n: Nat) -> Nat:\n  n\n"))
    assert definition.id == "Nat.id"
