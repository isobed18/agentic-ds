/**
 * The interface catalogue, and the two ways it goes wrong quietly.
 *
 * Both failures here are silent by construction. A duplicate key is legal
 * TypeScript -- the later entry simply wins, so a translation someone wrote
 * disappears with no error and only a build-time warning nobody reads. A
 * missing key is legal too, and renders the English source text.
 *
 * The second is a deliberate design choice and is asserted as such. The first
 * is a bug, and it has already happened once: "Needs attention" was added a
 * second time while filling in the untranslated screens, which overrode the
 * existing "Dikkat gerekiyor" with a different wording.
 */
import { describe, expect, it } from "vitest";
import SOURCE from "./i18n.ts?raw";
import { LANGUAGES, t } from "./i18n";

function catalogueKeys(): string[] {
  const start = SOURCE.indexOf("const TR: Record<string, string> = {");
  const end = SOURCE.indexOf("\n};", start);
  expect(start, "TR catalogue not found").toBeGreaterThan(-1);

  const keys: string[] = [];
  for (const line of SOURCE.slice(start, end).split("\n")) {
    const match = /^\s*"((?:[^"\\]|\\.)*)"\s*:/.exec(line);
    if (match) keys.push(match[1]);
  }
  return keys;
}

/**
 * Every `t("...")` literal in the app, with the file it came from.
 *
 * Read from source rather than exercised through the components: the point is
 * to cover strings nobody has rendered in a test yet, which is exactly where
 * the untranslated ones accumulate.
 */
function calledKeys(): Map<string, string[]> {
  const modules = import.meta.glob("../**/*.{ts,tsx}", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  const found = new Map<string, string[]>();
  for (const [path, source] of Object.entries(modules)) {
    // The catalogue defines keys; the tests deliberately look some up.
    if (path.endsWith("/i18n.ts") || /\.test\.tsx?$/.test(path)) continue;
    for (const match of source.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)*)"/g)) {
      found.set(match[1], [...(found.get(match[1]) ?? []), path]);
    }
  }
  return found;
}

describe("the Turkish catalogue", () => {
  it("translates every string the interface asks for", () => {
    // The regression this exists for: 115 keys reached `t()` with no Turkish
    // entry, so roughly a quarter of the interface rendered in English while
    // the language was set to Turkish. Nothing failed -- `t()` falls back to
    // its key by design -- so only reading the screens revealed it.
    const known = new Set(catalogueKeys());
    const untranslated = [...calledKeys().entries()]
      .filter(([key]) => !known.has(key))
      .map(([key, files]) => `${key}  (${[...new Set(files)].join(", ")})`)
      .sort();

    expect(untranslated, "these render in English when the language is Turkish").toEqual([]);
  });

  it("finds the call sites it is meant to be checking", () => {
    // Guards the guard: if the glob or the regex stops matching, the test
    // above passes over an empty set and proves nothing.
    expect(calledKeys().size).toBeGreaterThan(400);
  });

  it("has no duplicate key", () => {
    const keys = catalogueKeys();
    const seen = new Set<string>();
    const duplicated = keys.filter((key) => (seen.has(key) ? true : (seen.add(key), false)));
    expect(duplicated, "a later entry silently overrides an earlier translation").toEqual([]);
  });

  it("is not empty", () => {
    expect(catalogueKeys().length).toBeGreaterThan(500);
  });

  it("offers exactly the languages the picker shows", () => {
    expect(LANGUAGES.map((l) => l.code).sort()).toEqual(["en", "tr"]);
  });
});

describe("the status vocabulary", () => {
  // `status.ts` looks its labels up indirectly -- `t(REASON_LABELS[code])` --
  // so the literal scan above cannot see them. They are the words a reader
  // sees on every run, and an English one here is as visible as any other.
  it("has a Turkish entry for every status, verdict, and gate reason", () => {
    const source = import.meta.glob("./status.ts", {
      query: "?raw",
      import: "default",
      eager: true,
    })["./status.ts"] as string;

    const labels = [...source.matchAll(/^\s*\w+:\s*"((?:[^"\\]|\\.)*)"\s*,/gm)].map((m) => m[1]);
    expect(labels.length, "no label maps parsed out of status.ts").toBeGreaterThan(20);

    const known = new Set(catalogueKeys());
    expect(labels.filter((label) => !known.has(label))).toEqual([]);
  });
});

describe("translation", () => {
  it("renders Turkish for a key that has one", () => {
    expect(t("Needs attention")).toBe("Dikkat gerekiyor");
  });

  it("degrades an untranslated string to English rather than a placeholder", () => {
    const unknown = "A sentence nobody has translated";
    expect(t(unknown)).toBe(unknown);
  });

  it("interpolates parameters into the translated string, not the key", () => {
    // The Turkish word order differs from the English, so this would pass by
    // accident if the parameter were substituted before the lookup.
    expect(t("via {key}", { key: "customer_id" })).toBe("customer_id üzerinden");
  });

  it("leaves an unknown parameterised key readable", () => {
    expect(t("no such key {x}", { x: "7" })).toBe("no such key 7");
  });
});
