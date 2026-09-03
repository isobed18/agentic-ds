/**
 * The measured schema is drawn, and nothing draws it from behind a dead route.
 *
 * #434: the product had no picture of how the tables connect. The overlay
 * showed a flat text list of joins and nothing visual, while two renderers
 * that draw it properly sat in the tree unreachable -- `SchemaDiagram` behind
 * `DataReview <- IntakeStage <- StageWorkspace <- Workflows` and behind
 * `Explore`, `SchemaMap` behind `StageWorkspace`. Both chains stopped being
 * routed at e04a5e8, which turned the catalogue pages into redirects and left
 * their components in the tree. Nothing failed. The feature simply left.
 *
 * So there are two assertions here, and the second is the one that matters
 * long-term: a component nobody can reach is a component that can be silently
 * lost, and only a reachability check notices.
 */
import { describe, expect, it } from "vitest";

import SOURCE from "./UnderstandingWorkspace.tsx?raw";

/** Every module under `web/src`, so reachability is computed and not listed. */
const MODULES = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** This file's own directory, which every glob key is relative to. */
const HERE = "components";

/**
 * A relative specifier resolved against a directory, as a key rooted at
 * `web/src` with no extension -- so `./ui` from `components/Shell` and
 * `../components/ui.tsx` from `pages/Automation` are the same string.
 *
 * Vite normalises a glob key for a file beside the globbing module to `./x`
 * and everything else to `../dir/x`, so both forms have to go through this.
 */
function resolve(fromDirectory: string, specifier: string): string {
  const parts = fromDirectory ? fromDirectory.split("/") : [];
  for (const segment of specifier.replace(/\?.*$/, "").split("/")) {
    if (segment === "." || segment === "") continue;
    else if (segment === "..") parts.pop();
    else parts.push(segment);
  }
  return parts.join("/").replace(/\.tsx?$/, "");
}

function directoryOf(key: string): string {
  return key.split("/").slice(0, -1).join("/");
}

/** Module key -> the raw source behind it. */
const SOURCE_BY_KEY = new Map(
  Object.entries(MODULES).map(([path, text]) => [resolve(HERE, path), text]),
);

/** What one module imports, as keys in the same namespace. */
function importsOf(key: string): string[] {
  const text = SOURCE_BY_KEY.get(key) ?? "";
  return [...text.matchAll(/from\s+"(\.[^"]+)"/g)].map((match) =>
    resolve(directoryOf(key), match[1]),
  );
}

/** Everything the app can actually reach from its entry points. */
function reachable(): Set<string> {
  const seen = new Set<string>();
  const queue = ["App", "main"].filter((key) => SOURCE_BY_KEY.has(key));
  while (queue.length) {
    const key = queue.pop()!;
    if (seen.has(key)) continue;
    seen.add(key);
    for (const next of importsOf(key)) if (!seen.has(next)) queue.push(next);
  }
  return seen;
}

describe("the measured schema is a picture again (#434)", () => {
  it("mounts the schema diagram in the relationships section", () => {
    // Both overlays that carry "Measured relationships" -- the structured node
    // and the synthesis summary -- and the list stays beneath it as the
    // per-edge evidence it already was.
    expect(SOURCE).toContain('import { SchemaDiagram } from "./SchemaDiagram"');
    expect(
      SOURCE.match(/<MeasuredSchema profile=\{profile\} \/><RelationshipList/g) ?? [],
    ).toHaveLength(2);
    expect(SOURCE).toContain(
      "<SchemaDiagram tables={profile.tables} relationships={relationships} />",
    );
  });

  it("needs no data the overlay does not already hold", () => {
    // `profile.relationships` is the same `MeasuredRelationship[]` the text
    // list is built from, so nothing is recomputed to draw it.
    expect(SOURCE).toContain("const relationships = profile.relationships ?? [];");
  });

  it("draws nothing rather than an empty canvas", () => {
    // A single-table source has no join to draw, and an empty graph frame
    // reads as a broken panel instead of as "there is nothing here".
    expect(SOURCE).toContain(
      "if (profile.tables.length < 2 || !relationships.length) return null;",
    );
  });
});

describe("no component sits behind a dead route (#434)", () => {
  it("finds the module graph it is meant to be checking", () => {
    // Guards the guard: a glob or regex that stopped matching would let the
    // assertion below pass over an empty set and prove nothing.
    const live = reachable();
    expect(SOURCE_BY_KEY.size).toBeGreaterThan(80);
    expect(live.size).toBeGreaterThan(40);
    expect(live).toContain("components/UnderstandingWorkspace");
    expect(live).toContain("components/SchemaDiagram");
    expect(live).toContain("pages/Automation");
  });

  it("reaches every component and page from the routed app", () => {
    // This is the check whose absence let a whole feature disappear: the
    // routes were retired, the components that hung off them were left in the
    // tree, and every suite stayed green while the schema graph was gone.
    const live = reachable();
    const orphaned = [...SOURCE_BY_KEY.keys()]
      .filter((key) => key.startsWith("components/") || key.startsWith("pages/"))
      .filter((key) => !/\.test$/.test(key))
      .filter((key) => !live.has(key))
      .sort();

    expect(orphaned, "these render nothing because nothing imports them").toEqual([]);
  });
});
