"""CLI wrapper: generate the synthetic messy sample dataset.

The builders live in :mod:`ads.testing.sample_data` so tests and this script
share one definition.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from ads.testing.sample_data import write_sample_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/sample"), help="Output directory")
    args = parser.parse_args()

    out = write_sample_dataset(args.out)
    transactions = pd.read_parquet(out / "transactions.parquet")
    ledger_rows = sum(1 for _ in (out / "ledger_2019_2024.csv").open(encoding="utf-8")) - 1

    print(f"Wrote sample data to {out.resolve()}")
    print("  physicians.xlsx        2 sheets (title rows, null column, PII columns)")
    print(f"  ledger_2019_2024.csv   {ledger_rows:,} rows (semicolon-delimited)")
    print(
        f"  transactions.parquet   {len(transactions):,} rows, "
        f"{int(transactions['flagged'].sum())} flagged "
        f"({transactions['flagged'].mean():.2%})"
    )


if __name__ == "__main__":
    main()
