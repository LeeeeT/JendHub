"""Compares jend.parser with the parser of Bend 2 on the files of the mirror.

Bend's parser runs in a Node container, from a checkout of bendlang/bend.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from jend import base, corpus, hub, mirror
from jend.loader import FileKey, Library, load_all
from jend.mirror import Base, label
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
        (
            "T: term_higher((REC(k, tele_bind(params.concat(fs), K)),"
            " tele_bind(params.concat(fs), tip))) };\n        FAM[c] = k;"
        ),
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


def packages(files: Path, commit: str) -> dict[str, list[str]]:
    return {
        folder.name: sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*.bend"))
        for folder in sorted(files.iterdir())
        if folder.is_dir() and folder.name != commit
    }


def prepare(
    bend: Path, files: Path, commit: str, hashes: list[str], names: dict[str, str], work: Path
) -> None:
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(bend / "bend2", work / "bend2")
    source = (bend / "bend2" / "bend.ts").read_text(encoding="utf-8")
    for old, new in RECORDS:
        if source.count(old) != 1:
            raise SystemExit(f"bend.ts changed; update RECORDS for: {old}")
        source = source.replace(old, new)
    (work / "bend2" / "bend_ref.ts").write_text(source + RECORDER, encoding="utf-8", newline="\n")
    shutil.copy(HERE / "reference.ts", work / "bend2" / "reference.ts")
    shutil.copy(hub.cached_path(files, commit, base.PATH), work / "bend2" / "base.bend")
    (work / "lib" / "names").mkdir(parents=True)
    for package_hash in hashes:
        shutil.copytree(files / package_hash, work / "lib" / package_hash)
    for name, package_hash in names.items():
        (work / "lib" / "names" / name).write_text(package_hash + "\n", encoding="utf-8")


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
    files = args.data / mirror.FILES
    snapshot = mirror.load(args.data / mirror.MIRROR)
    commit = snapshot.base.commit
    on_disk = packages(files, commit)
    chosen = [origin.hash for origin in corpus.select(snapshot) if not isinstance(origin, Base)]
    targets = [
        (package_hash, path)
        for package_hash in (on_disk if args.all else chosen)
        for path in on_disk[package_hash]
    ]
    names = {
        label(package): package.hash for package in snapshot.packages if package.label is not None
    }
    prepare(args.bend, files, commit, list(on_disk), names, args.work)
    (args.work / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    expected = reference(args.work)

    base_key: FileKey = (commit, base.PATH)
    keys = [base_key, *((h, path) for h, paths in on_disk.items() for path in paths)]
    sources = {key: hub.cached_path(files, *key).read_text(encoding="utf-8") for key in keys}
    loader = load_all(Library(sources, names, base_key), targets)
    refs = loader.references()
    elsewhere: dict[str, set[str]] = {}
    for module in loader.modules.values():
        for declaration in module.declarations:
            if declaration.fills and module_of(declaration.key) != module.ns:
                used = loader.resolve(module.names[declaration.key])
                elsewhere.setdefault(declaration.key, set()).update(used)

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
