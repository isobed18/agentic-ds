"""The language layer, and the two ways it can silently break.

The suite as a whole runs pinned to English (see conftest), so without these
the Turkish path would never be exercised and could rot unnoticed.
"""

from __future__ import annotations

import pytest

from ads.api import i18n


def test_header_and_query_forms_all_resolve() -> None:
    assert i18n.normalise("tr") == "tr"
    assert i18n.normalise("TR") == "tr"
    assert i18n.normalise("tr-TR,tr;q=0.9,en;q=0.8") == "tr"
    assert i18n.normalise("en-GB,en;q=0.9") == "en"
    assert i18n.normalise(None) == i18n.DEFAULT
    assert i18n.normalise("de") == i18n.DEFAULT, "unknown language falls back, not raises"


def test_turkish_renders_and_english_is_the_source() -> None:
    with i18n.using("tr"):
        assert i18n.t("Class balance") == "Sınıf dengesi"
    with i18n.using("en"):
        assert i18n.t("Class balance") == "Class balance"


def test_untranslated_text_degrades_to_english_not_to_a_placeholder() -> None:
    """The failure mode that matters: a missing key must still read as a
    sentence. A blank or `??key??` panel is worse than an English one."""
    with i18n.using("tr"):
        assert i18n.t("A sentence nobody has translated") == "A sentence nobody has translated"


def test_language_does_not_leak_between_contexts() -> None:
    """Two readers on one server can be in different languages, so exiting a
    context must restore the previous one rather than reset to the default."""
    outer = i18n.current()
    with i18n.using("tr"):
        assert i18n.current() == "tr"
        with i18n.using("en"):
            assert i18n.current() == "en"
        assert i18n.current() == "tr", "inner context must not overwrite the outer one"
    assert i18n.current() == outer


def test_no_catalogue_entry_is_evaluated_at_import_time() -> None:
    """A module-level `_t(...)` freezes one language into the process.

    This is not hypothetical: the split-protection labels were written as a
    module-level dict, which meant every reader saw whichever language happened
    to be active when the module was first imported.
    """
    import ads.api.panels as panels

    source = __import__("pathlib").Path(panels.__file__).read_text(encoding="utf-8")
    module_level = [
        line
        for line in source.splitlines()
        if "_t(" in line and line and not line[0].isspace() and not line.startswith(("def ", "#"))
    ]
    assert module_level == [], f"translated at import time: {module_level}"


@pytest.mark.parametrize("language", i18n.SUPPORTED)
def test_every_supported_language_has_a_catalogue(language: str) -> None:
    with i18n.using(language):
        assert isinstance(i18n.t("Class balance"), str)
