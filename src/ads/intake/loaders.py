"""Deterministic loaders for messy enterprise files.

No LLM is involved. Parsing an Excel file is a parser problem, not a reasoning
problem.

The governing principle is risk R6 from the architecture report: **surface
messiness, don't silently repair it.** Every repair this module performs (a
skipped title row, a renamed duplicate column, a dropped empty column) is
recorded as a :class:`LoadIssue` so it reaches the human at the stage-0/1 gate
instead of quietly changing the meaning of the data.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ads.contracts.datacard import LoadIssue

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
PARQUET_SUFFIXES = {".parquet", ".pq"}

_MAX_HEADER_SCAN_ROWS = 20
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass
class LoadedTable:
    """A table read from disk, plus everything that went wrong reading it."""

    name: str
    frame: pd.DataFrame
    source_uri: str
    source_format: str
    sheet_name: str | None = None
    issues: list[LoadIssue] = field(default_factory=list)

    def add_issue(self, severity: str, code: str, detail: str) -> None:
        self.issues.append(LoadIssue(severity=severity, code=code, detail=detail))


def _slugify(value: str) -> str:
    """Normalise a header to snake_case, splitting camelCase rather than folding it.

    `movieId` used to become `movieid`, which erased the word boundary and with
    it every downstream heuristic that reads names as tokens: the identifier
    check looked for an `id` token and found a single blob, so an integer
    `movieId` was profiled as a continuous measurement and relationship
    detection reported no joins at all on a dataset whose joins were complete.

    Splitting first yields `movie_id`, which is both the convention the rest of
    the codebase uses and the form those heuristics were written against.
    """
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", expanded).strip("_").lower()
    return slug or "unnamed"


def normalize_columns(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[LoadIssue]]:
    """Normalize column names, recording every change.

    Handles the three things messy exports reliably produce: blank/``Unnamed:``
    headers from merged cells, duplicate names, and whitespace padding.
    """
    issues: list[LoadIssue] = []
    seen: dict[str, int] = {}
    new_names: list[str] = []

    for idx, raw in enumerate(frame.columns):
        text = "" if raw is None else str(raw).strip()
        if not text or text.lower().startswith("unnamed:") or text.lower() == "nan":
            name = f"column_{idx}"
            issues.append(
                LoadIssue(
                    severity="warn",
                    code="blank_column_name",
                    detail=f"Column at position {idx} had no usable header; named {name!r}.",
                )
            )
        else:
            name = _slugify(text)
            if name != text:
                issues.append(
                    LoadIssue(
                        severity="info",
                        code="column_renamed",
                        detail=f"Renamed {text!r} -> {name!r}.",
                    )
                )

        if name in seen:
            seen[name] += 1
            deduped = f"{name}_{seen[name]}"
            issues.append(
                LoadIssue(
                    severity="warn",
                    code="duplicate_column_name",
                    detail=f"Duplicate column {name!r} at position {idx}; renamed {deduped!r}.",
                )
            )
            name = deduped
        else:
            seen[name] = 0
        new_names.append(name)

    out = frame.copy()
    out.columns = new_names
    return out, issues


def _drop_empty(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[LoadIssue]]:
    """Drop all-null rows and columns, which Excel exports produce in bulk."""
    issues: list[LoadIssue] = []
    empty_cols = [c for c in frame.columns if frame[c].isna().all()]
    if empty_cols:
        frame = frame.drop(columns=empty_cols)
        issues.append(
            LoadIssue(
                severity="warn",
                code="empty_columns_dropped",
                detail=f"Dropped {len(empty_cols)} all-null column(s): {empty_cols[:10]}",
            )
        )
    before = len(frame)
    frame = frame.dropna(how="all")
    if len(frame) < before:
        issues.append(
            LoadIssue(
                severity="info",
                code="empty_rows_dropped",
                detail=f"Dropped {before - len(frame)} all-null row(s).",
            )
        )
    return frame.reset_index(drop=True), issues


def _infer_header_row(raw: pd.DataFrame) -> int:
    """Find the most likely header row in a sheet with leading title/junk rows.

    Heuristic: the first row whose cells are mostly non-null strings and whose
    following row is at least as populated. Enterprise exports routinely put a
    report title and a blank line above the real header.
    """
    best_row = 0
    best_score = -1.0
    scan = min(_MAX_HEADER_SCAN_ROWS, len(raw))

    for i in range(scan):
        row = raw.iloc[i]
        non_null = row.notna().sum()
        if non_null == 0:
            continue
        fill_ratio = non_null / max(len(row), 1)
        string_ratio = sum(isinstance(v, str) for v in row) / max(non_null, 1)
        # A header should be well-populated and textual.
        score = fill_ratio * 0.6 + string_ratio * 0.4
        # Prefer rows followed by data rows of similar width.
        if i + 1 < len(raw):
            next_fill = raw.iloc[i + 1].notna().sum() / max(len(row), 1)
            if next_fill < fill_ratio * 0.5:
                score *= 0.5
        if score > best_score:
            best_score, best_row = score, i
        if score >= 0.9:
            break

    return best_row


def _sniff_csv(path: Path) -> tuple[str, str]:
    """Return (encoding, delimiter), falling back safely."""
    encoding = _ENCODINGS[0]
    sample = ""
    for enc in _ENCODINGS:
        try:
            with path.open("r", encoding=enc) as fh:
                sample = fh.read(64_000)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    delimiter = ","
    if sample:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
            delimiter = max(counts, key=lambda k: counts[k]) if any(counts.values()) else ","
    return encoding, delimiter


def load_csv(path: str | Path, *, name: str | None = None) -> LoadedTable:
    path = Path(path)
    encoding, delimiter = _sniff_csv(path)
    table = LoadedTable(
        name=name or _slugify(path.stem),
        frame=pd.DataFrame(),
        source_uri=str(path.resolve()),
        source_format="csv",
    )
    if encoding != "utf-8-sig":
        table.add_issue("info", "encoding_fallback", f"Read using encoding {encoding!r}.")
    if delimiter != ",":
        table.add_issue("info", "delimiter_detected", f"Detected delimiter {delimiter!r}.")

    # pandas silently mangles duplicate headers to `a`, `a.1`, ... before we can
    # see them. Read the raw header first so a genuine duplicate in the source is
    # reported as a warning rather than disappearing into a rename.
    raw_header = pd.read_csv(
        path, encoding=encoding, sep=delimiter, nrows=1, header=None, skipinitialspace=True
    )
    original_names = [str(v).strip() for v in raw_header.iloc[0]] if len(raw_header) else []
    duplicates = {n for n in original_names if original_names.count(n) > 1}
    for name in sorted(duplicates):
        table.add_issue(
            "warn",
            "duplicate_column_name",
            f"Source header contains {original_names.count(name)} columns named {name!r}; "
            "they were given distinct names. Verify which one is authoritative.",
        )

    frame = pd.read_csv(
        path, encoding=encoding, sep=delimiter, low_memory=False, skipinitialspace=True
    )
    frame, norm_issues = normalize_columns(frame)
    frame, empty_issues = _drop_empty(frame)
    table.frame = frame
    table.issues.extend(norm_issues + empty_issues)
    return table


def load_parquet(path: str | Path, *, name: str | None = None) -> LoadedTable:
    path = Path(path)
    frame = pd.read_parquet(path)
    frame, norm_issues = normalize_columns(frame)
    return LoadedTable(
        name=name or _slugify(path.stem),
        frame=frame,
        source_uri=str(path.resolve()),
        source_format="parquet",
        issues=norm_issues,
    )


def load_excel(path: str | Path, *, name_prefix: str | None = None) -> list[LoadedTable]:
    """Load every sheet of a workbook as a separate table.

    Sheets are independent tables far more often than they are pages of one
    table, so each becomes its own DataCard and the SchemaDiscoveryAgent decides
    how they relate.
    """
    path = Path(path)
    prefix = name_prefix or _slugify(path.stem)
    book = pd.read_excel(path, sheet_name=None, header=None)

    tables: list[LoadedTable] = []
    for sheet_name, raw in book.items():
        table = LoadedTable(
            name=f"{prefix}__{_slugify(sheet_name)}" if len(book) > 1 else prefix,
            frame=pd.DataFrame(),
            source_uri=str(path.resolve()),
            source_format="excel",
            sheet_name=str(sheet_name),
        )

        if raw.empty:
            table.add_issue("warn", "empty_sheet", f"Sheet {sheet_name!r} is empty; skipped.")
            table.frame = pd.DataFrame()
            tables.append(table)
            continue

        header_row = _infer_header_row(raw)
        if header_row > 0:
            table.add_issue(
                "warn",
                "header_row_inferred",
                f"Header detected at row {header_row} of sheet {sheet_name!r}; "
                f"{header_row} leading row(s) treated as title/junk. Verify this is correct.",
            )

        frame = raw.iloc[header_row + 1 :].copy()
        frame.columns = raw.iloc[header_row]
        frame, norm_issues = normalize_columns(frame)
        frame, empty_issues = _drop_empty(frame)
        # Excel gives everything back as object dtype; recover real types.
        frame = frame.infer_objects()
        for col in frame.columns:
            if frame[col].dtype == object:
                converted = pd.to_numeric(frame[col], errors="coerce")
                if converted.notna().sum() >= frame[col].notna().sum() * 0.95:
                    frame[col] = converted

        table.frame = frame
        table.issues.extend(norm_issues + empty_issues)
        tables.append(table)

    return tables


def load_path(path: str | Path) -> list[LoadedTable]:
    """Dispatch on file extension. Returns a list because a workbook is many tables."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return [load_csv(path)]
    if suffix in EXCEL_SUFFIXES:
        return load_excel(path)
    if suffix in PARQUET_SUFFIXES:
        return [load_parquet(path)]
    raise ValueError(f"Unsupported file type: {path.name} (suffix {suffix!r})")


def load_directory_with_failures(
    directory: str | Path,
) -> tuple[list[LoadedTable], dict[str, str]]:
    """Load every supported file, and report the ones that could not be read.

    One unreadable file used to abort the whole directory. `.txt` is routed to
    the delimited-text loader, so a prose README next to four clean CSVs raised
    `Expected 1 fields in line 4, saw 5` out of here and the caller turned it
    into a 400 -- the entire source became unprofilable because of a file nobody
    wanted profiled. Uploading a folder that happens to contain a README was
    enough to make the product unusable.

    Failures are returned rather than raised, keyed by file name, so the caller
    can show the file as unreadable and carry on with the rest. That is the
    project's stated invariant: surface partial failures, never let a source
    silently disappear -- and never let one file take the others down.
    """
    directory = Path(directory)
    supported = CSV_SUFFIXES | EXCEL_SUFFIXES | PARQUET_SUFFIXES
    tables: list[LoadedTable] = []
    failures: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        if not (path.is_file() and path.suffix.lower() in supported):
            continue
        try:
            tables.extend(load_path(path))
        except Exception as exc:  # noqa: BLE001 - any reader failure is per-file news
            failures[path.name] = str(exc).strip() or exc.__class__.__name__
    return tables, failures


def load_directory(directory: str | Path) -> list[LoadedTable]:
    """Load every supported file in a directory, sorted for deterministic order.

    Unreadable files are skipped. Use `load_directory_with_failures` when the
    caller needs to tell someone which file was dropped and why.
    """
    tables, _ = load_directory_with_failures(directory)
    return tables
