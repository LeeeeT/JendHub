"""Compares jend.parser with the parser of Bend 2 on the files of the mirror.

Bend's parser runs in a Node container, from a checkout of bendlang/bend.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from jend import corpus, hub, mirror
from jend.loader import Library, load_all
from jend.parser import module_of

HERE = Path(__file__).parent
NODE_IMAGE = "node:26-slim"
RECORDS = [
    (
        "T: term_higher(tele_bind(tele, parse_term(p))), v: null",
        "T: term_higher(REC(k, tele_bind(tele, parse_term(p)))), v: null",
    ),
    (
        "term_higher(body_flatten(parse_body(p), vars, () => p.frs++))",
        "term_higher(REC(k, body_flatten(parse_body(p), vars, () => p.frs++)))",
    ),
    (
        "T: term_higher(tele_bind(params, K)), c: cs",
        "T: term_higher(REC(k, tele_bind(params, K))), c: cs",
    ),
    (
        "T: term_higher(tele_bind(params.concat(fs), tip)) };",
        "T: term_higher(REC(k, tele_bind(params.concat(fs), tip))) };\n        FAM[c] = k;",
    ),
    ("T: term_higher(T), v: null, x: tc", "T: term_higher(REC(k, T)), v: null, x: tc"),
]
RECORDER = """
export const RECS: Map<Name, LTerm[]> = new Map();
export const FAM: Record<Name, Name> = Object.create(null);
export function REC(k: Name, t: LTerm): LTerm {
  if (!RECS.has(k)) {
    RECS.set(k, []);
  }
  RECS.get(k)!.push(t);
  return t;
}
"""


def prepare(bend: Path, snapshot: mirror.Mirror, data: Path, work: Path) -> None:
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(bend / "bend2", work / "bend2")
    source = (bend / "bend2" / "bend.ts").read_text(encoding="utf-8")
    for old, new in RECORDS:
        if source.count(old) != 1:
            raise SystemExit(f"bend.ts changed; update RECORDS for: {old}")
        source = source.replace(old, new)
    (work / "bend2" / "bend_ref.ts").write_text(source + RECORDER, encoding="utf-8", newline="\n")
    shutil.copy(HERE / "reference.ts", work / "bend2" / "reference.ts")
    shutil.copy(
        hub.cached_path(data / mirror.FILES, snapshot.base.hash, snapshot.base.files[0].path),
        work / "bend2" / "base.bend",
    )
    (work / "lib" / "names").mkdir(parents=True)
    for package in snapshot.packages:
        shutil.copytree(data / mirror.FILES / package.hash, work / "lib" / package.hash)
        if package.name is not None:
            names = work / "lib" / "names" / f"{package.name}@{package.version}"
            names.write_text(package.hash + "\n", encoding="utf-8")


def reference(work: Path) -> dict[tuple[str, str], dict[str, object]]:
    script = (
        "cp -r /work/bend2 /tmp/bend2 && cp -r /work/lib /tmp/lib && cd /tmp/bend2"
        " && ulimit -s unlimited && node --stack-size=200000 reference.ts"
        " /work/targets.json /work/reference.json"
    )
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "-v", f"{work.resolve()}:/work",
         "-e", "BEND_LIB=/tmp/lib", NODE_IMAGE, "sh", "-c", script],
        check=True,
    )  # fmt: skip
    found = json.loads((work / "reference.json").read_text(encoding="utf-8"))
    return {(entry["hash"], entry["path"]): entry for entry in found}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bend", type=Path, required=True, help="a checkout of bendlang/bend")
    parser.add_argument("--data", type=Path, default=Path("data/hub"))
    parser.add_argument("--work", type=Path, default=Path("data/hub/conformance"))
    parser.add_argument("--all", action="store_true", help="all package versions, not the corpus")
    args = parser.parse_args()
    snapshot = mirror.load(args.data / mirror.MIRROR)
    packages = [*snapshot.packages] if args.all else corpus.select(snapshot)[1:]
    targets = [(package.hash, file.path) for package in packages for file in package.files]
    prepare(args.bend, snapshot, args.data, args.work)
    (args.work / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    expected = reference(args.work)

    every = [snapshot.base, *snapshot.packages]
    sources = {
        (package.hash, file.path): hub.cached_path(
            args.data / mirror.FILES, package.hash, file.path
        ).read_text(encoding="utf-8")
        for package in every
        for file in package.files
    }
    names = {f"{p.name}@{p.version}": p.hash for p in snapshot.packages if p.name is not None}
    loader = load_all(
        Library(sources, names, (snapshot.base.hash, snapshot.base.files[0].path)), targets
    )
    refs = loader.references()
    elsewhere: dict[str, set[str]] = {}
    for module in loader.modules.values():
        for declaration in module.declarations:
            if declaration.fills and module_of(declaration.key) != module.ns:
                elsewhere.setdefault(declaration.key, set()).update(loader.uses(declaration))

    failures = 0
    for target in targets:
        bend = expected[target]
        accepted = target in loader.modules
        if bool(bend["ok"]) != accepted:
            failures += 1
            print(f"{target[1]}: Bend accepts {bend['ok']}, jend accepts {accepted}")
            continue
        if not accepted:
            continue
        module = loader.modules[target]
        expected_refs = bend["refs"]
        assert isinstance(expected_refs, dict)
        own = {d.key for d in module.declarations if module_of(d.key) == module.ns}
        if own != set(expected_refs):
            failures += 1
            print(f"{target[1]}: other declarations")
        for key in own & set(expected_refs):
            used = refs.get(key, set())
            seen = set(expected_refs[key])
            if not seen <= used or not used - seen <= elsewhere.get(key, set()):
                failures += 1
                print(f"{key}: Bend {sorted(seen)}, jend {sorted(used)}")
    print(f"{len(targets)} files, {failures} differences")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
