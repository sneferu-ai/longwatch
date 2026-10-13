"""LongWatch — offline footage triage and evidence-integrity ledger.

The package is deliberately flat: one module per load-bearing structure of the
specification, no ORM, no broker.  See ``spec.md`` at the repository root.
"""

__version__ = "0.1.0"

INTEGRITY_STATEMENT = (
    "The SHA-256 digests in this bundle show that each listed excerpt file is "
    "byte-identical to material cut from a source file whose digest LongWatch "
    "recorded when it read that file, and that the accompanying custody record "
    "has not changed since each entry was written. They show nothing about what "
    "happened to the footage before LongWatch received it, nothing about when it "
    "was recorded, and nothing about whether depicted conduct was lawful, humane, "
    "or otherwise. LongWatch makes exactly one claim: the listed time ranges "
    "deserve examination by a qualified person, and the measured reasons are "
    "attached."
)

TIME_BASIS = (
    "server-local clock at processing time; not synchronized to any external "
    "time authority"
)


def review_medium(long_edge: int) -> str:
    """The disclosure string that travels on every manifest and CSV row."""
    return (
        f"lossy working copy (long edge <= {int(long_edge)}px, CRF 28); "
        "decisions were rendered on this working copy"
    )
