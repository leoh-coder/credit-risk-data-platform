"""Helpers for the few places where DuckDB needs SQL text instead of bind parameters.

Table functions (read_parquet, glob), ATTACH and identifiers cannot be bound with `?`. Everything
interpolated goes through one of these two functions, and lake-provided names are allow-listed.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def safe_identifier(name: str) -> str:
    """Entity names come from lake manifests (external input): never interpolate them unchecked."""
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"unsafe SQL identifier from the lake: {name!r}")
    return name
