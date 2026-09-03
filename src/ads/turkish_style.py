"""House Turkish for agent-authored prose.

Almost every Turkish sentence a reader sees on a run is written by a model, not
looked up in a catalogue: the agents are asked for `_tr` fields alongside the
English ones (``ads/agents/interpretation.py`` and friends), so the wording is
not ours to fix the way ``ads/api/i18n.py`` entries are.

That leaves one recurring failure. Asked for "document corpus", a model reaches
for *belge külliyatı* -- a literal rendering that is not what a Turkish speaker
calls a set of uploaded files (#406). *Külliyat* is the collected-works sense,
used of an author's complete output, and it reads as archaic and faintly odd
applied to three PDFs somebody dragged onto an upload box.

Two halves, because neither alone is enough:

* :data:`TURKISH_PROSE_INSTRUCTION` goes into the prompts, so the models stop
  producing it. That is the real fix, but it only governs future runs and only
  as reliably as any instruction does.
* :func:`house_turkish` rewrites it at the contract boundary, so a run that
  produced it anyway -- or one persisted before this existed -- still reads
  correctly. This is deliberately a very short list: it is a spelling
  correction for a known tic, not a translation layer.
"""

from __future__ import annotations

import re

#: Appended to the Turkish-writing instructions the agents already carry. It
#: names the tic rather than restating "write natural Turkish", which every one
#: of those prompts already says and which did not prevent this.
TURKISH_PROSE_INSTRUCTION = (
    "In Turkish, call a set of uploaded documents 'belgeler' or 'belge kümesi'. "
    "Never 'belge külliyatı': 'külliyat' means an author's collected works and "
    "reads as archaic and wrong for uploaded files."
)

#: Turkish suffixes harmonize with the vowels of the stem they attach to.
#: `külliyat` is a back-vowel word and `küme` a front-vowel one, so swapping
#: the stem means swapping every vowel in the suffix that followed it --
#: "külliyatında" becomes "kümesinde", not "kümesinda".
_FRONTED = str.maketrans("ıauo", "ieüö")

#: `külliyat`, either bare or carrying its possessive `ı` and whatever suffix
#: chain follows it: külliyatı, külliyatını, külliyatında, külliyatındaki,
#: külliyatının, külliyatıyla, külliyatıdır. `küme` takes the same chain with
#: the same buffers -- its possessive is `si` -- so fronting the vowels of the
#: tail is the whole conversion, and one rule covers the paradigm.
#:
#: A case ending on the bare stem (`külliyata`, `külliyatta`) is deliberately
#: left alone: `küme` ends in a vowel, so those forms take different buffers
#: entirely and a blind swap would produce a non-word. The trailing word
#: boundary is what makes the rule decline them rather than mangle them; they
#: are not shapes the reported phrase takes.
_KULLIYAT = re.compile(r"külliyat(ı[a-zçğıöşü]*)?\b", re.IGNORECASE)


def _rewrite(match: re.Match[str]) -> str:
    suffix = match.group(1)
    replacement = "küme" if suffix is None else "kümes" + suffix.lower().translate(_FRONTED)
    # A sentence-initial "Külliyatı" stays capitalized; the word is otherwise
    # written exactly as the paradigm above spells it.
    return replacement.capitalize() if match.group(0)[:1].isupper() else replacement


def house_turkish(text: str) -> str:
    """`text` with the known Turkish tics rewritten, unchanged if it has none.

    Applied to stored prose rather than at render time so the correction is in
    the artifact a person can read back, and so every surface -- the API, the
    Markdown report, an export -- gets it without having to remember to.
    """
    if not text:
        return text
    return _KULLIYAT.sub(_rewrite, text)
