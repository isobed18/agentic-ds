"""Stage 0 (Intake & Profiling) and the deterministic half of Stage 1.

Nothing in this package calls an LLM. Parsing files, computing statistics and
measuring key overlap are solved problems; per the architecture report's
LLM/deterministic split, a model that touched them would only add errors.
"""

from ads.intake.keys import (
    KeyDetectionOptions,
    detect_primary_keys,
    detect_relationships,
    measure_relationship,
    relationships_digest,
)
from ads.intake.loaders import (
    LoadedTable,
    load_csv,
    load_directory,
    load_directory_with_failures,
    load_excel,
    load_parquet,
    load_path,
    normalize_columns,
)
from ads.intake.profiler import (
    ProfileOptions,
    assess_sensitivity,
    classify_sensitivity,
    datacard_digest,
    infer_semantic_type,
    profile_column,
    profile_table,
    profile_tables,
)

__all__ = [
    "KeyDetectionOptions",
    "LoadedTable",
    "ProfileOptions",
    "assess_sensitivity",
    "classify_sensitivity",
    "datacard_digest",
    "detect_primary_keys",
    "detect_relationships",
    "infer_semantic_type",
    "load_csv",
    "load_directory",
    "load_directory_with_failures",
    "load_excel",
    "load_parquet",
    "load_path",
    "measure_relationship",
    "normalize_columns",
    "profile_column",
    "profile_table",
    "profile_tables",
    "relationships_digest",
]
