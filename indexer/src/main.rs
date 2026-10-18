use std::{
    collections::{BTreeSet, HashMap},
    error::Error,
    io::{self, Read, Write},
    path::Path,
};

use bend2_lsp::frontend::{Disk, Env, ErrorText, Load, LoadedFile, Meaning, load};
use serde::{Deserialize, Serialize};

/// Which files to load: each entry as the compiler loads the file it checks,
/// with packages from `lib` and Base from `base`.
#[derive(Deserialize)]
struct Request {
    lib: String,
    base: String,
    entries: Vec<String>,
}

#[derive(Serialize)]
struct Response {
    entries: Vec<Entry>,
}

#[derive(Serialize)]
struct Entry {
    path: String,
    error: Option<Failure>,
    declarations: BTreeSet<String>,
    uses: BTreeSet<Use>,
}

/// Why a load stopped: what the loader expected, what it observed, and the
/// canonical path and the 1-based line of the file where it stopped.
#[derive(Debug, Eq, PartialEq, Serialize)]
struct Failure {
    expected: String,
    observed: Option<String>,
    file: Option<String>,
    line: Option<usize>,
}

/// A declaration of the entry that uses another declaration.
#[derive(Eq, Ord, PartialEq, PartialOrd, Serialize)]
struct Use {
    from: Declaration,
    to: Declaration,
}

/// A declaration: the canonical path of its file and its name there.
#[derive(Clone, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
struct Declaration {
    file: String,
    name: String,
}

fn text(file: &LoadedFile, beg: usize, end: usize) -> String {
    String::from_utf16_lossy(file.parsed.get(beg..end).unwrap_or(&[]))
}

/// The definition that each book key of a load names, by the key and by
/// whether it is a constructor's. A constructor names the type that declares
/// it, since a type and its constructors are one definition.
fn declarations(loaded: &Load) -> HashMap<(String, bool), Declaration> {
    let mut out = HashMap::new();
    for file in &loaded.files {
        for occurrence in &file.occurrences {
            if let Meaning::Declaration { key, ctr: false } = &occurrence.meaning {
                let name = text(file, occurrence.span.beg, occurrence.span.end);
                let file = file.real.clone();
                out.insert((key.clone(), false), Declaration { file, name });
            }
        }
    }
    for file in &loaded.files {
        for occurrence in &file.occurrences {
            if let Meaning::Declaration { key, ctr: true } = &occurrence.meaning
                && let Some(owner) = loaded
                    .tlds
                    .get(occurrence.tld)
                    .and_then(|tld| tld.name.clone())
                    .and_then(|name| out.get(&(name, false)).cloned())
            {
                out.insert((key.clone(), true), owner);
            }
        }
    }
    out
}

fn uses(loaded: &Load) -> BTreeSet<Use> {
    let mut found = BTreeSet::new();
    let Some(entry) = loaded
        .entry
        .as_ref()
        .and_then(|path| loaded.files.iter().rfind(|file| file.real == *path))
    else {
        return found;
    };
    let declared = declarations(loaded);
    for occurrence in &entry.occurrences {
        let Meaning::Global { key, ctr } = &occurrence.meaning else {
            continue;
        };
        let owner = loaded
            .tlds
            .get(occurrence.tld)
            .and_then(|tld| tld.name.clone())
            .and_then(|name| declared.get(&(name, false)));
        let (Some(from), Some(to)) = (owner, declared.get(&(key.clone(), *ctr))) else {
            continue;
        };
        if from != to {
            found.insert(Use {
                from: from.clone(),
                to: to.clone(),
            });
        }
    }
    found
}

/// The names of the top-level declarations of the entry, without constructors.
fn entry_declarations(loaded: &Load) -> BTreeSet<String> {
    loaded
        .entry
        .as_ref()
        .and_then(|path| loaded.files.iter().rfind(|file| file.real == *path))
        .map(|entry| {
            entry
                .occurrences
                .iter()
                .filter(|occurrence| {
                    matches!(occurrence.meaning, Meaning::Declaration { ctr: false, .. })
                })
                .map(|occurrence| text(entry, occurrence.span.beg, occurrence.span.end))
                .collect()
        })
        .unwrap_or_default()
}

fn line_at(source: &str, offset: usize) -> usize {
    1 + source
        .encode_utf16()
        .take(offset)
        .filter(|unit| *unit == u16::from(b'\n'))
        .count()
}

fn failure(loaded: &Load) -> Option<Failure> {
    let error = loaded.error.as_ref()?;
    let place = error
        .at
        .as_ref()
        .zip(loaded.error_text)
        .and_then(|((_, beg, _), text)| match text {
            ErrorText::Parsed(index) => loaded
                .files
                .get(index)
                .map(|file| (file.real.clone(), line_at(&file.source, file.source_offset(*beg)))),
            ErrorText::Source(index) => loaded
                .reading
                .get(index)
                .map(|file| (file.real.clone(), line_at(&file.source, *beg))),
        });
    let (file, line) = place.unzip();
    Some(Failure {
        expected: error.exp.clone(),
        observed: error.obs.clone(),
        file,
        line,
    })
}

fn real(path: &str) -> String {
    std::fs::canonicalize(Path::new(path)).map_or_else(
        |_| path.to_owned(),
        |real| real.to_string_lossy().into_owned(),
    )
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input)?;
    let request: Request = serde_json::from_str(&input)?;
    let env = Env {
        lib: request.lib.clone(),
        base: real(&request.base),
        hub: "https://hub.bend-lang.com".to_owned(),
    };
    let mut entries = Vec::with_capacity(request.entries.len());
    for entry in request.entries {
        let loaded = load(&Disk, &env, &entry);
        entries.push(Entry {
            declarations: entry_declarations(&loaded),
            uses: uses(&loaded),
            error: failure(&loaded),
            path: entry,
        });
    }
    let mut output = io::stdout().lock();
    serde_json::to_writer(&mut output, &Response { entries })?;
    output.write_all(b"\n")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture {
        root: std::path::PathBuf,
    }

    impl Fixture {
        fn new(name: &str) -> Self {
            let root =
                std::env::temp_dir().join(format!("jendhub-index-{name}-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("lib/0xaa"))
                .unwrap_or_else(|error| panic!("{error}"));
            std::fs::create_dir_all(root.join("lib/0xbb"))
                .unwrap_or_else(|error| panic!("{error}"));
            Self { root }
        }

        fn write(&self, path: &str, text: &str) -> String {
            let path = self.root.join(path);
            std::fs::write(&path, text).unwrap_or_else(|error| panic!("{error}"));
            real(&path.to_string_lossy())
        }

        fn load(&self, entry: &str) -> Load {
            let env = Env {
                lib: self.root.join("lib").to_string_lossy().into_owned(),
                base: self.write("base.bend", BASE),
                hub: String::new(),
            };
            load(&Disk, &env, entry)
        }

        fn uses(&self, entry: &str) -> Vec<(String, String, String, String)> {
            uses(&self.load(entry))
                .into_iter()
                .map(|found| {
                    (
                        found.from.file,
                        found.from.name,
                        found.to.file,
                        found.to.name,
                    )
                })
                .collect()
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    const BASE: &str = "type Nat is Data:\n  Zero{}\n  Succ{pred: Nat}\n\ndef Nat.add(a: Nat, b: Nat) -> Nat:\n  a\n";

    #[test]
    fn uses_cross_modules_and_reach_base() {
        let fixture = Fixture::new("local");
        let lib = fixture.write(
            "lib/0xaa/box.bend",
            "import Base\n\ntype Box is Data:\n  Box{value: Nat}\n\ndef open(b: Box) -> Nat:\n  match b:\n    case Box{v}:\n      v\n",
        );
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport ./box.bend as B\n\ndef root(x: B.Box) -> Nat:\n  (B.open(x) + Zero{} : Nat)\n",
        );
        let found = fixture.uses(&main);
        assert!(found.contains(&(main.clone(), "root".into(), lib.clone(), "open".into())));
        assert!(found.contains(&(main.clone(), "root".into(), lib.clone(), "Box".into())));
        let base = fixture.root.join("base.bend");
        let base = real(&base.to_string_lossy());
        assert!(found.contains(&(main.clone(), "root".into(), base.clone(), "Nat".into())));
        assert!(fixture.uses(&lib).contains(&(
            lib.clone(),
            "open".into(),
            lib.clone(),
            "Box".into()
        )));
    }

    #[test]
    fn a_constructor_use_is_a_use_of_its_type() {
        let fixture = Fixture::new("constructor");
        let shapes = fixture.write(
            "lib/0xaa/shapes.bend",
            "import Base\n\ntype Shape is Data:\n  Circle{radius: Nat}\n",
        );
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport ./shapes.bend as S\n\ndef unit() -> S.Shape:\n  S.Circle{Zero{}}\n",
        );
        let found: Vec<_> = fixture
            .uses(&main)
            .into_iter()
            .filter(|(_, _, file, _)| file == &shapes)
            .collect();
        assert_eq!(
            found,
            vec![(main.clone(), "unit".into(), shapes, "Shape".into())]
        );
    }

    #[test]
    fn an_entry_that_stops_in_an_import_has_no_uses() {
        let fixture = Fixture::new("stopped");
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport ./missing.bend as M\n\ndef root(x: Nat) -> Nat:\n  Nat.add(x, x)\n",
        );
        assert!(fixture.uses(&main).is_empty());
    }

    #[test]
    fn a_failure_names_the_import_line_that_stops_the_load() {
        let fixture = Fixture::new("missing");
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport ./missing.bend as M\n\ndef root(x: Nat) -> Nat:\n  x\n",
        );
        let found = failure(&fixture.load(&main));
        assert_eq!(found.as_ref().and_then(|found| found.file.clone()), Some(main));
        assert_eq!(found.and_then(|found| found.line), Some(2));
    }

    #[test]
    fn a_failure_names_the_imported_file_that_does_not_parse() {
        let fixture = Fixture::new("broken");
        let broken = fixture.write(
            "lib/0xaa/broken.bend",
            "import Base\n\ntype A is Data:\n  Wait{}\n\ntype B is Data:\n  Wait{}\n",
        );
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport ./broken.bend as X\n\ndef root(x: Nat) -> Nat:\n  x\n",
        );
        let found = failure(&fixture.load(&main));
        assert_eq!(found.as_ref().and_then(|found| found.file.clone()), Some(broken));
        assert_eq!(found.and_then(|found| found.line), Some(7));
    }

    #[test]
    fn a_load_without_an_error_has_no_failure() {
        let fixture = Fixture::new("fine");
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\n\ndef root(x: Nat) -> Nat:\n  x\n",
        );
        assert_eq!(failure(&fixture.load(&main)), None);
    }

    #[test]
    fn declarations_name_the_top_level_declarations_of_the_entry() {
        let fixture = Fixture::new("declarations");
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\n\ntype Shape is Data:\n  Circle{radius: Nat}\n\ndef area(s: Shape) -> Nat:\n  Zero{}\n",
        );
        let found = entry_declarations(&fixture.load(&main));
        assert_eq!(found, ["Shape".to_owned(), "area".to_owned()].into());
    }

    #[test]
    fn a_local_shadows_a_declaration() {
        let fixture = Fixture::new("shadow");
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\n\ndef name(n: Nat) -> Nat:\n  n\n\ndef root(x: Nat) -> Nat:\n  name = x\n  name\n",
        );
        assert!(
            fixture
                .uses(&main)
                .iter()
                .all(|(_, _, _, name)| name != "name")
        );
    }

    #[test]
    fn uses_reach_other_packages() {
        let fixture = Fixture::new("other");
        let far = fixture.write(
            "lib/0xbb/lib.bend",
            "import Base\n\ndef far(n: Nat) -> Nat:\n  n\n",
        );
        let main = fixture.write(
            "lib/0xaa/main.bend",
            "import Base\nimport 0xbb/lib.bend as L\n\ndef root(x: Nat) -> Nat:\n  L.far(x)\n",
        );
        assert!(
            fixture
                .uses(&main)
                .contains(&(main.clone(), "root".into(), far, "far".into()))
        );
    }
}
