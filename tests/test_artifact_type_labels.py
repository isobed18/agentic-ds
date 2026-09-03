"""The web app's artifact-kind labels, held to the `ArtifactType` vocabulary.

Every artifact card, artifact list row and artifact modal names its artifact by
its kind. That label used to be derived from the type code in TypeScript --
``integration_plan`` -> ``"Integration plan"`` -- and passed to the translator
through a variable, which made it invisible to the catalogue test that scans for
``t("literal")`` call sites. So 29 of the kinds reached a Turkish screen with no
Turkish entry and the whole suite stayed green (#366).

The labels are written out by hand now, which fixes the initialisms English
readers saw too ("Eda report", "Rl feature report"). A hand-written table can
drift from the enum, and the drift would be silent in the same way -- a new kind
would simply fall back to the derived English. `ArtifactType` is a closed set by
contract, so this file enumerates it and fails instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from ads.contracts.base import ArtifactType

LABEL_MODULE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "artifactTypeLabel.ts"
)


def _declared_labels() -> dict[str, str]:
    """Read the `LABELS` table out of the module as `{type code: English label}`.

    Parsed from source rather than copied here: a copy would keep passing after
    the module drifted, which is the bug being guarded.
    """
    source = LABEL_MODULE.read_text(encoding="utf-8")
    block = re.search(r"const LABELS[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert block, "LABELS table not found -- this test needs updating with the module"

    labels = dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))
    assert labels, "no labels parsed out of the LABELS table"
    return labels


def test_every_artifact_kind_has_a_written_label() -> None:
    declared = _declared_labels()
    missing = sorted(kind.value for kind in ArtifactType if kind.value not in declared)
    assert missing == [], (
        "these artifact kinds fall back to a derived English label and have no "
        "Turkish translation: " + ", ".join(missing)
    )


def test_no_label_names_a_kind_that_no_longer_exists() -> None:
    known = {kind.value for kind in ArtifactType}
    stale = sorted(code for code in _declared_labels() if code not in known)
    assert stale == [], "these labels name kinds the contract dropped: " + ", ".join(stale)


def test_initialisms_are_not_title_cased() -> None:
    # The mechanical derivation cannot know "EDA" is not a word, which is what
    # made English readers see "Eda report". Written labels can, so pin it.
    assert _declared_labels()[ArtifactType.EDA_REPORT.value] == "EDA report"
