from pathlib import Path

from jendhub.hub.api import Listing, Package, Release
from jendhub.hub.base import Base
from jendhub.hub.library import Library

TAG = "v2.0.37"
LIB = Release("lib", "1.0.0.0")
OTHER = Release("other", "1.0.0.0")
LIB_HASH = "0x" + "a" * 32
OTHER_HASH = "0x" + "b" * 32

BASE = """\
type Nat is Data:
  Zero{}
  Succ{pred: Nat}

def Nat.add(a: Nat, b: Nat) -> Nat:
  a
"""

BOX = """\
import Base

type Box is Data:
  Box{value: Nat}

# Opens a box.
def open(b: Box) -> Nat:
  match b:
    case Box{v}:
      v
"""


def library(root: Path, packages: dict[Release, tuple[str, dict[str, str]]]) -> Library:
    found = Library(root)
    found.store_base(Base(TAG, BASE.encode()))
    for release, (hash_, files) in packages.items():
        encoded = {path: text.encode() for path, text in files.items()}
        found.store_package(Package(Listing(hash_, release), encoded))
    return found
