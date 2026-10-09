// Loads each target file with Bend's own parser, as `bend check` does, and
// prints which files it accepts and the names that each declaration uses.
import * as fs from "node:fs";
import * as Bend from "./bend_ref.ts";

const lib = process.env.BEND_LIB as string;
const targets: Array<[string, string]> = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out: Array<object> = [];

function names(terms: any[]): Set<string> {
  const found = new Set<string>();
  const stack: any[] = [...terms];
  const pattern = (q: any): void => {
    if (q.$ === "PCtr") {
      found.add(q.k);
      q.x.forEach(pattern);
    }
  };
  while (stack.length > 0) {
    const t = stack.pop();
    switch (t.$) {
      case "Var": if (t.v !== undefined) stack.push(t.v); break;
      case "Ref": found.add(t.k); break;
      case "Sub": pattern(t.v); stack.push(t.f); break;
      case "Let": stack.push(...t.v, t.f); break;
      case "Typ": stack.push(t.g); break;
      case "Min": stack.push(t.a, t.b); break;
      case "All": stack.push(t.A, t.B); break;
      case "Lam": stack.push(t.f); break;
      case "App": stack.push(t.f, t.x); break;
      case "ADT": found.add(t.k); stack.push(...t.x); break;
      case "Ctr": found.add(t.k); stack.push(...t.x); break;
      case "Mat": found.add(t.k); stack.push(t.h, t.m); break;
      case "Eql": stack.push(t.a, t.b, t.T); break;
      case "Rwt": stack.push(t.e, t.p, t.f); break;
      case "Ann": stack.push(t.x, t.T); break;
    }
  }
  return found;
}

for (const [hash, path] of targets) {
  Bend.RECS.clear();
  const book = Bend.book_nil();
  const ns = hash + "/" + path.replace(/\.bend$/, "");
  try {
    await Bend.book_load(book, lib + "/" + hash + "/" + path, ns, new Map());
    const refs: Record<string, string[]> = {};
    for (const [k, terms] of Bend.RECS) {
      if (!k.startsWith(ns + ":")) {
        continue;
      }
      const used = new Set<string>();
      for (const n of names(terms)) {
        const target = Bend.FAM[n] ?? n;
        if (book.tlds[target] !== undefined) {
          used.add(target);
        }
      }
      refs[k] = [...used].sort();
    }
    out.push({ hash, path, ok: true, refs });
  } catch (e: any) {
    out.push({ hash, path, ok: false, why: String(e?.exp ?? e?.message ?? e).slice(0, 200) });
  }
}
fs.writeFileSync(process.argv[3], JSON.stringify(out));
