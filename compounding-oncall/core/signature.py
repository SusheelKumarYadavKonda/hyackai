"""Failure signature extraction.

Normalise a raw stderr blob into a stable fingerprint of the *failure mode*,
independent of which table, partition, or timestamp produced it.
"""

import hashlib
import re

# Order matters: broad patterns last, so specific ones claim their text first.
VOLATILE: list[tuple[str, str]] = [
    (r"\d{4}-\d{2}-\d{2}[T ]?[\d:.]*", "<TS>"),          # timestamps / partition dates
    (r"'[^']*\.parquet'", "<PATH>"),                      # quoted parquet paths
    (r"[\w./-]+\.parquet", "<PATH>"),                     # bare parquet paths
    (r"\brow \d+\b", "row <N>"),                          # row ids
    (r"\bpartition[= ][\w-]+", "partition <P>"),          # partition identifiers
    (r"\b\d+\.\d+%", "<PCT>"),                            # percentages
    (r"\b\d{3,}\b", "<N>"),                               # long numerics
]

# Failure-mode labels. Matched against the *normalised* text so that wording
# differences between jobs still land on the same diagnostic.
SIG_TYPES: list[tuple[str, str]] = [
    ("NULL_FLOOD", r"not null|null constraint|null rate|nulls in column"),
    ("VOLUME_COLLAPSE", r"row count|volume|too few rows|empty partition"),
    ("SCHEMA_DRIFT", r"schema|column .*missing|dtype|unexpected column"),
]


def normalize(error_text: str, table: str | None = None) -> str:
    """Strip everything volatile from an error string.

    When `table` is given, occurrences of the table name are replaced with a
    placeholder. This is not cosmetic: real job output names the table in the
    job id ("job_orders_daily") and in the assertion text ("in orders:"), so
    merely keeping it out of the hash input is not enough to make the same
    failure mode match across tables.
    """
    s = error_text.strip()

    if table:
        # Longest-first so 'job_orders_daily' is claimed before 'orders'.
        variants = sorted({f"job_{table}_daily", f"{table}_daily", table}, key=len, reverse=True)
        for variant in variants:
            s = re.sub(rf"\b{re.escape(variant)}\b", "<TABLE>", s, flags=re.IGNORECASE)

    for pattern, replacement in VOLATILE:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).lower()


def signature(error_text: str, table: str, include_table: bool = False) -> str:
    """Hash a normalised error into a stable signature.

    include_table=False is deliberate and load-bearing. The same failure mode
    must produce the same signature across different tables, otherwise replay
    never fires and there is no compounding curve. A test asserts this.
    """
    base = normalize(error_text, table)
    if include_table:
        base = f"{table}:{base}"
    return "sig_" + hashlib.sha1(base.encode()).hexdigest()[:12]


def sig_type(error_text: str) -> str:
    """Classify the failure mode, which selects the diagnostic to run."""
    normalised = normalize(error_text)
    for label, pattern in SIG_TYPES:
        if re.search(pattern, normalised, flags=re.IGNORECASE):
            return label
    return "UNKNOWN"
