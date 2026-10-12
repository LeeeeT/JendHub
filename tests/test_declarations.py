from jendhub.declarations import declared_names

SOURCE = """\
import Base
import kit@0.1.0.0/laws.bend as Laws

# def commented
type Pair<a> is Data:
  Pair{fst: Nat, snd: Nat}

def Pair.swap(p: Pair) -> Pair:
  def_inner = p
  def_inner

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


def test_declared_names_reads_top_level_declarations() -> None:
    assert declared_names(SOURCE) == {"Pair", "Pair.swap", "loop", "swap_twice"}
