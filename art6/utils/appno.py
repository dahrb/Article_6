"""Shared handling of HUDOC application-number fields.

The `appno` field packs several application numbers into one string with
inconsistent separators, so both the collection and the metadata-processing
stages have to normalise it the same way before splitting.
"""

from __future__ import annotations

import re

_NON_APPNO_CHARS = re.compile(r"[^0-9/ ;]")
_REPEATED_SEPARATORS = re.compile(r";+")


def clean_appnos(text: str) -> str:
    """Normalise an `appno` string to a single `;`-delimited list.

    Anything that is not a digit, `/`, space or `;` becomes a separator, spaces
    are treated as separators too, then runs of separators are collapsed and
    trimmed from both ends.
    """
    cleaned = _NON_APPNO_CHARS.sub(";", text)
    cleaned = cleaned.replace(" ", ";")
    return _REPEATED_SEPARATORS.sub(";", cleaned).strip(";")
