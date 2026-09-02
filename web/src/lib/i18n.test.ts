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
import CATALOG_SOURCE from "../pages/Catalog.tsx?raw";
import PROJECT_CONTENTS_SOURCE from "../components/ProjectContents.tsx?raw";
import SHELL_SOURCE from "../components/Shell.tsx?raw";
import STAGING_ROUTING_SOURCE from "../components/stagingRoutingState.ts?raw";
import UNDERSTANDING_WORKSPACE_SOURCE from "../components/UnderstandingWorkspace.tsx?raw";
import SOURCE from "./i18n.ts?raw";
import { COUNT_UNITS } from "../components/collectionUnit";
import { GROUPS } from "../components/mlPipelineGroups";
import { LANGUAGES, localizedList, t } from "./i18n";

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

describe("labels the scanner cannot see", () => {
  /**
   * The catalogue test above reads `t("literal")` out of the source, so a label
   * that reaches `t()` through a variable is invisible to it. That is not a
   * hypothetical: the five guided steps on the upload page and these six stage
   * titles are both rendered as `t(variable)`, and both sat untranslated while
   * the suite stayed green (#38).
   *
   * Fixing every dynamic call site is a larger change than the bug warrants.
   * Pinning the lists that actually feed one is not, and it is the same list a
   * Turkish reader sees on the pipeline graph.
   */
  it("translates every stage title on the pipeline graph", () => {
    const known = new Set(catalogueKeys());
    const visibleText = GROUPS.flatMap((group) => [group.title, group.description]);
    const missing = visibleText.filter((text) => !known.has(text));
    expect(missing, "stage titles and descriptions reaching t() through variables").toEqual([]);
  });

  it("translates every unit an artifact collection count can carry", () => {
    // #297: the count phrase is chosen by the collection's key and reaches t()
    // through a variable, so the literal scan cannot see any of these. An
    // untranslated one would ship a Turkish artifact dialog saying "42 columns".
    const known = new Set(catalogueKeys());
    expect(COUNT_UNITS.filter((phrase) => !known.has(phrase))).toEqual([]);
  });

  it("pins the top-level navigation labels the scanner cannot see", () => {
    // NAV reaches t() through `label`, so the literal scanner cannot see these
    // destinations at all (#77). #111 reduces the top level to Home, Projects
    // and Settings; pin the labels so they stay translated and named as the
    // customer expects ("Projects", not the internal "automation").
    expect(SHELL_SOURCE).toContain('{ to: "/", label: "Home"');
    expect(SHELL_SOURCE).toContain('{ to: "/projects", label: "Projects"');
    expect(SHELL_SOURCE).toContain('{ to: "/settings", label: "Settings"');
  });

  it("translates every staging progress label and detail", () => {
    // Staging labels reach t() through RoutingSubstep fields, so the general
    // literal-call scanner cannot see them. Pin the source list directly; an
    // untranslated detail would otherwise ship quietly in English (#43).
    const visibleText = [...STAGING_ROUTING_SOURCE.matchAll(/\b(?:label|detail):\s*"((?:[^"\\]|\\.)*)"/g)]
      .map((match) => match[1]);
    expect(visibleText.length, "no staging labels parsed").toBeGreaterThan(5);
    const known = new Set(catalogueKeys());
    expect(visibleText.filter((label) => !known.has(label))).toEqual([]);
    expect(UNDERSTANDING_WORKSPACE_SOURCE).toContain("{t(step.detail)}");
    expect(UNDERSTANDING_WORKSPACE_SOURCE).toContain("{t(secondary)}");
  });
});

describe("catalogue screens request translations at the render boundary", () => {
  it("translates the minimal home's orientation copy at the render boundary", () => {
    // #301 replaced the project catalogue with a minimal welcome page. Keep
    // its scope-defining copy behind t() so Turkish does not silently fall back
    // to the English source text.
    expect(CATALOG_SOURCE).toContain(
      't("Your data-science work starts inside a project.")',
    );
  });

  it("translates the automation's selected-data page", () => {
    // #157 replaced the project-as-automation summary table with the exact file
    // snapshot selected for one child automation. Pin the new scope-defining
    // copy at its render site so it cannot silently fall back to English.
    expect(PROJECT_CONTENTS_SOURCE).toContain('t("Selected automation data")');
    expect(PROJECT_CONTENTS_SOURCE).toContain('t("Only these project files belong to this automation.")');
  });
});

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

    // STATUS_TONES maps a state to how it looks, not to a word anyone reads
    // (#195). Every other quoted value in the file is prose a reader sees.
    const prose = source.replace(/const STATUS_TONES[\s\S]*?\n};/, "");
    const labels = [...prose.matchAll(/^\s*\w+:\s*"((?:[^"\\]|\\.)*)"\s*,/gm)].map((m) => m[1]);
    expect(labels.length, "no label maps parsed out of status.ts").toBeGreaterThan(20);

    const known = new Set(catalogueKeys());
    expect(labels.filter((label) => !known.has(label))).toEqual([]);
  });
});

describe("translation", () => {
  it("renders Turkish for a key that has one", () => {
    expect(t("Needs attention")).toBe("Dikkat gerekiyor");
  });

  it("uses the reviewed Turkish wording for catalogue and quality labels", () => {
    // These entries existed, so coverage stayed green while the UI said the
    // wrong thing. Assert the wording a Turkish reader actually sees (#77).
    expect([
      t("Experiments"),
      t("Loading experiments…"),
      t("Sensitive"),
      t("sensitive"),
      t("inconsistent delimiters"),
      t("header row inferred"),
      t("Recommended continuation"),
    ]).toEqual([
      "Deneyler",
      "Deneyler yükleniyor…",
      "Kişisel veri",
      "kişisel veri",
      "tutarsız ayırıcılar",
      "başlık satırı olduğu çıkarımı yapıldı",
      "Önerilen sonraki adımlar",
    ]);
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

describe("server-composed prose", () => {
  // Document extraction warnings carry measured counts and are written during
  // a background stage, so they cannot go through the catalogue: both halves
  // arrive from the server as parallel lists and one is picked here.
  it("picks the Turkish half of a bilingual list", () => {
    expect(localizedList(["OCR was skipped."], ["OCR atlandı."])).toEqual(["OCR atlandı."]);
  });

  it("falls back per entry for a run stored before the Turkish half existed", () => {
    // Not a blank line, and not a shifted one: the fallback is positional.
    expect(localizedList(["first", "second"], ["birinci"])).toEqual(["birinci", "second"]);
    expect(localizedList(["only English"])).toEqual(["only English"]);
  });
});
