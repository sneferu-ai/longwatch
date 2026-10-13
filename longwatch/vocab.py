"""The forbidden lexicon — refusal #1 of §1, enforced in one module.

LongWatch emits an integer rank and one sentence of measured observables.  No
surface of the system may carry a welfare or legal conclusion.  The lexicon
lives here and nowhere else; the three enforcement points (pattern write,
decision note, rendered sentence before persist) all import from this module.
"""
from __future__ import annotations

import re

#: The complete forbidden lexicon, as stated in §1.
FORBIDDEN_TERMS: tuple[str, ...] = (
    "abuse",
    "cruel",
    "cruelty",
    "neglect",
    "suffering",
    "distress",
    "illegal",
    "unlawful",
    "violation",
    "torture",
)

# Word-prefix match: the term at a word boundary, allowing inflections
# ("abused", "violations", "illegally").  Case-insensitive.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(r"\b" + re.escape(term) + r"\w*", re.IGNORECASE))
    for term in FORBIDDEN_TERMS
)

#: Operator-facing guidance returned with every 422.
GUIDANCE = (
    "Describe measured observables (durations, class presences, motion "
    "magnitudes, loudness deltas) rather than conclusions."
)


class VocabBreach(ValueError):
    """Raised when text carries a forbidden term."""

    def __init__(self, term: str, text: str = ""):
        self.term = term
        self.text = text
        super().__init__(f"forbidden term {term!r} present in text")

    def as_detail(self) -> dict:
        return {"error": "vocabulary_breach", "term": self.term, "guidance": GUIDANCE}


def find_forbidden(text: str | None) -> str | None:
    """Return the first matched forbidden term, or ``None`` when clean."""
    if not text:
        return None
    for term, pattern in _PATTERNS:
        if pattern.search(text):
            return term
    return None


def is_clean(text: str | None) -> bool:
    return find_forbidden(text) is None


def check_text(text: str | None) -> None:
    """Raise :class:`VocabBreach` naming the matched term."""
    term = find_forbidden(text)
    if term is not None:
        raise VocabBreach(term, text or "")


def check_many(**fields: str | None) -> None:
    """Check several named text fields; the first breach raises."""
    for value in fields.values():
        check_text(value)
