"""Synthetic messy dataset from the architecture report's worked example.

Lives in the package (not just in ``scripts/``) so tests and the CLI generator
share one definition — a fixture that drifts from the demo data is worse than no
fixture.

Deliberately plants the traps the pipeline must catch:

* ``total_comp_ytd`` correlates ~0.98 with the ``annual_comp`` target
  → the leakage audit must catch it.
* ``ledger.provider_ref`` matches ``physician_id`` on ~94.2% of rows
  → a real FK with a business-meaningful 5.8% orphan rate for the human gate.
* ``transactions.flagged`` is positive at ~0.3%
  → fraud detection looks plausible but fails the statistical-support check.
* ``transactions`` repeats ``physician_id`` and spans 2019-2024
  → a random split is wrong; grouped-temporal is right.
* The workbook has title rows above the header, an all-null column, a constant
  column and PII columns → intake must surface all of it.

Deterministic: fixed seed, so fixtures are stable across runs and machines.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260811
N_PHYSICIANS = 800
N_LEDGER = 20_000
N_TRANSACTIONS = 15_000

#: Share of ledger rows whose provider_ref resolves to a known physician.
LEDGER_MATCH_RATE = 0.942

SPECIALTIES = [
    "cardiology",
    "oncology",
    "pediatrics",
    "radiology",
    "orthopedics",
    "neurology",
    "dermatology",
    "general practice",
]
CITIES = ["istanbul", "ankara", "izmir", "bursa", "antalya", "adana"]
MERCHANT_CATEGORIES = ["equipment", "pharma", "travel", "consulting", "lab services", "software"]
ACCOUNT_CODES = ["4000", "4100", "5000", "5200", "6000", "6100", "7000"]


def build_physicians(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (master_sheet, compensation_sheet) split across a workbook."""
    ids = np.arange(10_001, 10_001 + N_PHYSICIANS)
    specialty = rng.choice(SPECIALTIES, N_PHYSICIANS)
    years_exp = rng.integers(1, 35, N_PHYSICIANS)

    specialty_premium = dict(zip(SPECIALTIES, rng.normal(0, 40_000, len(SPECIALTIES)), strict=True))
    base = 220_000 + years_exp * 8_500
    premium = np.array([specialty_premium[s] for s in specialty])
    annual_comp = base + premium + rng.normal(0, 25_000, N_PHYSICIANS)
    annual_comp = np.round(np.clip(annual_comp, 90_000, None), 2)

    # The leakage trap: year-to-date comp is ~98% correlated with annual comp.
    total_comp_ytd = np.round(annual_comp * rng.uniform(0.94, 1.02, N_PHYSICIANS), 2)

    master = pd.DataFrame(
        {
            "Physician ID": ids,
            "Full Name": [f"Dr. Physician {i}" for i in range(N_PHYSICIANS)],
            "Email Address": [f"physician{i}@example-clinic.test" for i in range(N_PHYSICIANS)],
            "Specialty": specialty,
            "City": rng.choice(CITIES, N_PHYSICIANS),
            "License No": [f"LIC{rng.integers(10**8, 10**9)}" for _ in range(N_PHYSICIANS)],
            "Hire Date": pd.to_datetime("2005-01-01")
            + pd.to_timedelta(rng.integers(0, 6500, N_PHYSICIANS), unit="D"),
            "Region Code": "TR",  # constant column
            "Unused Column": np.nan,  # all-null column
        }
    )

    compensation = pd.DataFrame(
        {
            "Physician ID": ids,
            "Years Experience": years_exp,
            "Annual Comp": annual_comp,
            "Total Comp YTD": total_comp_ytd,
            "Bonus Eligible": rng.choice(["Y", "N"], N_PHYSICIANS, p=[0.6, 0.4]),
        }
    )

    # Realistic missingness in the target: ~11% unreported.
    missing_idx = rng.choice(N_PHYSICIANS, size=int(N_PHYSICIANS * 0.11), replace=False)
    compensation.loc[missing_idx, "Annual Comp"] = np.nan
    compensation.loc[missing_idx, "Total Comp YTD"] = np.nan

    return master, compensation


def build_ledger(rng: np.random.Generator, physician_ids: np.ndarray) -> pd.DataFrame:
    """Accounting ledger whose provider_ref matches physicians on ~94.2% of rows."""
    n_known = int(N_LEDGER * LEDGER_MATCH_RATE)
    known_refs = rng.choice(physician_ids, n_known)
    orphan_refs = rng.integers(90_000, 99_999, N_LEDGER - n_known)
    provider_ref = np.concatenate([known_refs, orphan_refs])
    rng.shuffle(provider_ref)

    entry_date = pd.to_datetime("2019-01-01") + pd.to_timedelta(
        rng.integers(0, 2190, N_LEDGER), unit="D"
    )

    return pd.DataFrame(
        {
            "entry_id": np.arange(1, N_LEDGER + 1),
            "provider_ref": provider_ref,
            "account_code": rng.choice(ACCOUNT_CODES, N_LEDGER),
            "amount": np.round(rng.lognormal(7.5, 1.2, N_LEDGER), 2),
            "entry_date": entry_date,
            "description": rng.choice(
                ["invoice", "reimbursement", "adjustment", "credit note", "fee"], N_LEDGER
            ),
        }
    )


def build_transactions(rng: np.random.Generator, physician_ids: np.ndarray) -> pd.DataFrame:
    """Transaction history: repeated entities over time, with a very rare label."""
    return pd.DataFrame(
        {
            "txn_id": [f"TXN{i:08d}" for i in range(1, N_TRANSACTIONS + 1)],
            "physician_id": rng.choice(physician_ids, N_TRANSACTIONS),
            "txn_date": pd.to_datetime("2019-01-01")
            + pd.to_timedelta(rng.integers(0, 2190, N_TRANSACTIONS), unit="D"),
            "amount": np.round(rng.lognormal(6.0, 1.5, N_TRANSACTIONS), 2),
            "merchant_category": rng.choice(MERCHANT_CATEGORIES, N_TRANSACTIONS),
            # ~0.3% positive rate: plausible-sounding but statistically unsupported.
            "flagged": (rng.random(N_TRANSACTIONS) < 0.003).astype(int),
        }
    )


def write_workbook(path: Path, master: pd.DataFrame, compensation: pd.DataFrame) -> None:
    """Write the workbook with junk title rows above the real header."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        master.to_excel(writer, sheet_name="Physician Master", index=False, startrow=2)
        sheet = writer.sheets["Physician Master"]
        sheet["A1"] = "CONFIDENTIAL - Physician Master Extract"
        sheet["A2"] = "Generated 2026-08-11"

        compensation.to_excel(writer, sheet_name="Compensation", index=False)


def write_sample_dataset(out_dir: str | Path) -> Path:
    """Generate the full sample dataset into ``out_dir``. Returns the directory."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(SEED)
    master, compensation = build_physicians(rng)
    physician_ids = master["Physician ID"].to_numpy()
    ledger = build_ledger(rng, physician_ids)
    transactions = build_transactions(rng, physician_ids)

    write_workbook(out / "physicians.xlsx", master, compensation)
    # Semicolon-delimited on purpose: exercises delimiter sniffing.
    ledger.to_csv(out / "ledger_2019_2024.csv", index=False, sep=";")
    transactions.to_parquet(out / "transactions.parquet", index=False)
    return out


__all__ = [
    "LEDGER_MATCH_RATE",
    "N_LEDGER",
    "N_PHYSICIANS",
    "N_TRANSACTIONS",
    "SEED",
    "build_ledger",
    "build_physicians",
    "build_transactions",
    "write_sample_dataset",
    "write_workbook",
]
