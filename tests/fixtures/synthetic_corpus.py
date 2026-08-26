"""Generate a mixed-source corpus whose correct interpretation is known exactly.

`assurance_v0` measures whether the gate makes defensible *decisions*. This
measures something earlier and separate: whether the system understands what it
was given at all -- which files are what, how they join, at what cardinality,
which columns are personal, which column would leak a target, and which of the
document content is trustworthy.

The point of generating rather than downloading is that the answer key is
derived from the construction, not annotated afterwards. A real dataset can only
be scored against somebody's opinion of its schema; here the join keys,
cardinalities, overlap rates, orphan rates, primary keys, and the leak are facts
of how the rows were built. Scoring is then exact rather than a judgement call.

The domain is deliberately unremarkable. Nothing being measured -- key overlap,
fan-out, PII shape, leakage -- depends on whether the rows describe orders or
patients, and pretending otherwise would only invite reading the model's
world-knowledge as though it were schema discovery.

Traps are included on purpose, each aimed at a specific claim the product makes:

* **Orphans.** 3% of orders reference a customer that does not exist. A system
  that reports a clean join has not measured it.
* **A false friend.** `support_tickets.agent_id` and `products.sku` share a name
  shape and a value range but no real relationship. Name affinity alone should
  not produce an edge.
* **A leak.** `orders.refund_issued` is written *from* the churn label. Any model
  trained on it scores near-perfectly and is worthless. The leakage audit exists
  for exactly this.
* **PII.** Real-shaped emails and Turkish national id numbers, so sensitivity
  classification is exercised against checksummable formats rather than column
  names alone.
* **A narrative TXT.** Prose, not a table. The architecture report records that
  TXT is currently routed as delimited text, so this file is expected to be
  misrouted today; it is here to make that regression visible when it is fixed.
* **A PDF.** A real text layer, a table, and a chart. The table's numbers are
  the true per-quarter totals of `orders.csv`, so cross-source synthesis has
  something checkable to find rather than something plausible to assert.

Usage:

    python benchmarks/understanding_v0/generate.py --out data/benchmark-understanding
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

SEED = 20260826

CITIES = ["Ankara", "Istanbul", "Izmir", "Bursa", "Antalya", "Konya", "Adana"]
CATEGORIES = ["kitchen", "outdoor", "office", "audio", "lighting"]
STATUSES = ["placed", "shipped", "delivered", "cancelled"]

N_CUSTOMERS = 400
N_PRODUCTS = 60
N_ORDERS = 2_400
N_TICKETS = 520
ORPHAN_RATE = 0.03


def _turkish_id(rng: random.Random) -> str:
    """An 11-digit number that satisfies the real checksum rules.

    Generated properly rather than as random digits so that a detector doing
    checksum validation and a detector doing "column is called tckn" are
    distinguishable. A benchmark that only rewards the second one teaches the
    wrong thing.
    """
    while True:
        digits = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
        odd = sum(digits[0:9:2])
        even = sum(digits[1:8:2])
        tenth = (odd * 7 - even) % 10
        eleventh = (sum(digits) + tenth) % 10
        return "".join(str(d) for d in [*digits, tenth, eleventh])


def build(out: Path) -> dict:
    rng = random.Random(SEED)
    out.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- customers
    customers = pd.DataFrame(
        {
            "customer_id": [f"C{i:05d}" for i in range(1, N_CUSTOMERS + 1)],
            "full_name": [f"Kisi {i}" for i in range(1, N_CUSTOMERS + 1)],
            "email": [f"user{i}@example.com" for i in range(1, N_CUSTOMERS + 1)],
            "national_id": [_turkish_id(rng) for _ in range(N_CUSTOMERS)],
            "city": [rng.choice(CITIES) for _ in range(N_CUSTOMERS)],
            "signup_date": [
                (date(2024, 1, 1) + timedelta(days=rng.randint(0, 700))).isoformat()
                for _ in range(N_CUSTOMERS)
            ],
        }
    )
    # The label the whole exercise is aimed at. Deliberately imbalanced, because
    # a balanced target hides whether the metric choice was thought about.
    churn = [1 if rng.random() < 0.22 else 0 for _ in range(N_CUSTOMERS)]
    customers["churned"] = churn
    churn_by_customer = dict(zip(customers["customer_id"], churn, strict=True))

    # A column with missing values and mixed types, so profiling has something
    # to repair and report rather than a uniformly clean frame.
    customers["loyalty_tier"] = [
        rng.choice(["gold", "silver", "bronze", None, "2"]) for _ in range(N_CUSTOMERS)
    ]

    # ---------------------------------------------------------------- products
    products = pd.DataFrame(
        {
            "sku": [f"SKU-{i:04d}" for i in range(1, N_PRODUCTS + 1)],
            "category": [rng.choice(CATEGORIES) for _ in range(N_PRODUCTS)],
            "list_price": [round(rng.uniform(40, 900), 2) for _ in range(N_PRODUCTS)],
        }
    )

    # ---------------------------------------------------------------- orders
    known_ids = list(customers["customer_id"])
    order_rows = []
    orphans = 0
    for i in range(1, N_ORDERS + 1):
        if rng.random() < ORPHAN_RATE:
            customer_id = f"C{rng.randint(90_000, 99_999):05d}"  # references nothing
            orphans += 1
        else:
            customer_id = rng.choice(known_ids)
        order_date = date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))
        order_rows.append(
            {
                "order_id": f"O{i:06d}",
                "customer_id": customer_id,
                "order_date": order_date.isoformat(),
                "amount": round(rng.uniform(20, 1500), 2),
                "status": rng.choice(STATUSES),
            }
        )
    orders = pd.DataFrame(order_rows)

    # The leak. Written from the label, not from anything a system could know at
    # prediction time. Correlation is strong but not perfect, because a perfect
    # duplicate of the target is trivially caught and proves less.
    orders["refund_issued"] = [
        1
        if churn_by_customer.get(row.customer_id, 0) == 1 and rng.random() < 0.86
        else (1 if rng.random() < 0.04 else 0)
        for row in orders.itertuples()
    ]

    # ---------------------------------------------------------------- items
    item_rows = []
    for order_id in orders["order_id"]:
        for _ in range(rng.randint(1, 4)):
            sku = rng.choice(list(products["sku"]))
            item_rows.append(
                {
                    "item_id": f"I{len(item_rows) + 1:07d}",
                    "order_id": order_id,
                    "sku": sku,
                    "quantity": rng.randint(1, 5),
                    "unit_price": float(
                        products.loc[products["sku"] == sku, "list_price"].iloc[0]
                    ),
                }
            )
    order_items = pd.DataFrame(item_rows)

    # ---------------------------------------------------------------- tickets
    # `agent_id` shares the SKU-#### shape on purpose. It is a support agent, not
    # a product, and nothing joins. Name/shape affinity must not invent an edge.
    tickets = pd.DataFrame(
        {
            "ticket_id": [f"T{i:06d}" for i in range(1, N_TICKETS + 1)],
            "customer_id": [rng.choice(known_ids) for _ in range(N_TICKETS)],
            "agent_id": [f"SKU-{rng.randint(1, N_PRODUCTS):04d}" for _ in range(N_TICKETS)],
            "opened_at": [
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 364))).isoformat()
                for _ in range(N_TICKETS)
            ],
            "satisfaction": [rng.choice([1, 2, 3, 4, 5, None]) for _ in range(N_TICKETS)],
        }
    )

    customers.to_csv(out / "customers.csv", index=False)
    products.to_csv(out / "products.csv", index=False)
    orders.to_csv(out / "orders.csv", index=False)
    order_items.to_csv(out / "order_items.csv", index=False)
    tickets.to_csv(out / "support_tickets.csv", index=False)

    # ---------------------------------------------------------------- narrative
    (out / "operations_note.txt").write_text(
        "Operations note, Q4 2025\n\n"
        "Delivery times worsened in Bursa and Konya after the regional depot move.\n"
        "Support volume rose accordingly, and refunds followed. Nothing in this\n"
        "note is tabular; it is prose, and treating it as a delimited table would\n"
        "be a routing mistake rather than a parsing one.\n",
        encoding="utf-8",
    )

    # ---------------------------------------------------------------- PDF
    quarters, totals = _quarterly_totals(orders)
    _write_pdf(out / "quarterly_review.pdf", quarters, totals, products)

    truth = _ground_truth(
        customers, products, orders, order_items, tickets, orphans, quarters, totals
    )
    (out / "ground_truth.json").write_text(
        json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return truth


def _quarterly_totals(orders: pd.DataFrame) -> tuple[list[str], list[float]]:
    dates = pd.to_datetime(orders["order_date"])
    grouped = orders.assign(quarter=dates.dt.quarter).groupby("quarter")["amount"].sum()
    return [f"2025-Q{q}" for q in grouped.index], [round(float(v), 2) for v in grouped.to_numpy()]


def _write_pdf(
    path: Path, quarters: list[str], totals: list[float], products: pd.DataFrame
) -> None:
    """A PDF with a real text layer, one table, and one chart.

    The table's numbers are the true quarterly totals of `orders.csv`. That is
    the whole reason the PDF exists: it gives cross-source synthesis a claim it
    can check against measured data instead of a plausible sentence to repeat.
    The chart carries values that appear *nowhere* in the CSVs, so a system that
    reports chart figures as trusted data can be caught doing it.
    """
    with PdfPages(path) as pdf:
        figure = plt.figure(figsize=(8.27, 11.69))
        figure.text(0.08, 0.94, "Quarterly Review 2025", fontsize=20, weight="bold")
        figure.text(
            0.08,
            0.90,
            "Revenue by quarter, reconciled against the order ledger.",
            fontsize=10,
        )

        table_axis = figure.add_axes([0.08, 0.62, 0.84, 0.24])
        table_axis.axis("off")
        table = table_axis.table(
            cellText=[[q, f"{t:,.2f}"] for q, t in zip(quarters, totals, strict=True)],
            colLabels=["Quarter", "Revenue (TRY)"],
            loc="center",
            cellLoc="left",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 1.6)

        chart_axis = figure.add_axes([0.12, 0.20, 0.76, 0.32])
        # Deliberately not derived from any CSV: a satisfaction index that exists
        # only as a picture. Nothing may treat these as measured rows.
        index_values = [72, 68, 61, 65]
        chart_axis.bar(quarters, index_values, color="#4C78A8")
        chart_axis.set_title("Customer satisfaction index (survey, not in the ledger)")
        chart_axis.set_ylim(0, 100)
        chart_axis.set_ylabel("index")

        figure.text(
            0.08,
            0.12,
            "The satisfaction index is survey-derived and appears in no table.\n"
            "It is chart-only content and must not enter training data.",
            fontsize=9,
        )
        pdf.savefig(figure)
        plt.close(figure)


def _ground_truth(
    customers: pd.DataFrame,
    products: pd.DataFrame,
    orders: pd.DataFrame,
    order_items: pd.DataFrame,
    tickets: pd.DataFrame,
    orphans: int,
    quarters: list[str],
    totals: list[float],
) -> dict:
    """The answer key, computed from the frames that were just written."""
    matched_orders = orders["customer_id"].isin(set(customers["customer_id"])).sum()
    return {
        "seed": SEED,
        "files": {
            "customers.csv": {"rows": len(customers), "route": "structured"},
            "products.csv": {"rows": len(products), "route": "structured"},
            "orders.csv": {"rows": len(orders), "route": "structured"},
            "order_items.csv": {"rows": len(order_items), "route": "structured"},
            "support_tickets.csv": {"rows": len(tickets), "route": "structured"},
            "quarterly_review.pdf": {"route": "documents"},
            # Prose. Recorded as the intended answer even though the system is
            # documented as getting this wrong today.
            "operations_note.txt": {"route": "documents", "currently_misrouted": True},
        },
        "primary_keys": {
            "customers.csv": ["customer_id"],
            "products.csv": ["sku"],
            "orders.csv": ["order_id"],
            "order_items.csv": ["item_id"],
            "support_tickets.csv": ["ticket_id"],
        },
        "relationships": [
            {
                "from": "orders.csv",
                "from_columns": ["customer_id"],
                "to": "customers.csv",
                "to_columns": ["customer_id"],
                "cardinality": "N:1",
                "overlap_rate": round(float(matched_orders) / len(orders), 4),
                "orphan_rows": orphans,
            },
            {
                "from": "order_items.csv",
                "from_columns": ["order_id"],
                "to": "orders.csv",
                "to_columns": ["order_id"],
                "cardinality": "N:1",
                "overlap_rate": 1.0,
                "orphan_rows": 0,
            },
            {
                "from": "order_items.csv",
                "from_columns": ["sku"],
                "to": "products.csv",
                "to_columns": ["sku"],
                "cardinality": "N:1",
                "overlap_rate": 1.0,
                "orphan_rows": 0,
            },
            {
                "from": "support_tickets.csv",
                "from_columns": ["customer_id"],
                "to": "customers.csv",
                "to_columns": ["customer_id"],
                "cardinality": "N:1",
                "overlap_rate": 1.0,
                "orphan_rows": 0,
            },
        ],
        "false_relationships": [
            {
                "from": "support_tickets.csv",
                "from_columns": ["agent_id"],
                "to": "products.csv",
                "to_columns": ["sku"],
                "why": "shape and value range coincide; the entities are unrelated",
            }
        ],
        "target": {"table": "customers.csv", "column": "churned", "task": "binary classification"},
        "leaking_columns": [
            {
                "table": "orders.csv",
                "column": "refund_issued",
                "why": "written from the churn label; unavailable at prediction time",
            }
        ],
        "personal_columns": [
            {"table": "customers.csv", "column": "email", "kind": "email"},
            {"table": "customers.csv", "column": "national_id", "kind": "turkish_national_id"},
            {"table": "customers.csv", "column": "full_name", "kind": "name"},
        ],
        "quality_issues": [
            {
                "table": "customers.csv",
                "column": "loyalty_tier",
                "issue": "missing values and a numeric string mixed into a categorical",
            },
            {"table": "support_tickets.csv", "column": "satisfaction", "issue": "missing values"},
        ],
        "document_facts": {
            "quarterly_review.pdf": {
                "table_reconciles_with": "orders.csv quarterly sum of amount",
                "quarters": quarters,
                "revenue_totals": totals,
                "chart_only_values": {
                    "series": "Customer satisfaction index",
                    "values": [72, 68, 61, 65],
                    "appears_in_any_table": False,
                },
            }
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    truth = build(args.out)
    print(f"Wrote the corpus to {args.out}")
    for name in sorted(truth["files"]):
        print(f"  {name}")
    print(f"  ground_truth.json  ({len(truth['relationships'])} true relationships)")


if __name__ == "__main__":
    main()
