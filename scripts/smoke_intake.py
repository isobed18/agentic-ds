"""End-to-end smoke run of the deterministic intake spine.

Loads a directory, profiles every table, measures relationships, and prints the
exact text projection an agent would receive. Useful for eyeballing whether the
DataCard digest is compact enough to fit a local model's context budget.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ads.intake import (
    datacard_digest,
    detect_primary_keys,
    detect_relationships,
    load_directory,
    profile_tables,
    relationships_digest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/sample"))
    args = parser.parse_args()

    tables = load_directory(args.data)
    print(f"LOADED {len(tables)} table(s):")
    for t in tables:
        print(f"  {t.name:32s} {t.frame.shape[0]:>7,} x {t.frame.shape[1]:<3} "
              f"({t.source_format}{', sheet=' + t.sheet_name if t.sheet_name else ''})")

    cards = profile_tables(tables)
    frames = {t.name: t.frame for t in tables}

    print("\n" + "=" * 78)
    print("DATACARDS (this is what the agent sees)")
    print("=" * 78)
    total_chars = 0
    for card in cards:
        digest = datacard_digest(card)
        total_chars += len(digest)
        print()
        print(digest)

    print("\n" + "=" * 78)
    print("PRIMARY KEY CANDIDATES")
    print("=" * 78)
    for card in cards:
        keys = detect_primary_keys(card, frames[card.table_name])
        if keys:
            for k in keys:
                print(f"  {k.table}.{'+'.join(k.columns)}  distinct={k.n_distinct:,} "
                      f"clean={k.is_clean_key}")
        else:
            print(f"  {card.table_name}: (none found)")

    print("\n" + "=" * 78)
    rels = detect_relationships(cards, frames)
    print(relationships_digest(rels))

    print("\n" + "=" * 78)
    print(f"Total DataCard context: {total_chars:,} chars (~{total_chars // 4:,} tokens)")
    print("=" * 78)


if __name__ == "__main__":
    main()
