"""Helper for the few places where DuckDB needs SQL text instead of bind parameters.

Table functions (read_parquet, glob), ATTACH and identifiers cannot be bound with `?`. Every interpolated
literal goes through `quote_literal`; the only interpolated identifiers are the allow-listed entity names
(`loader.lake.ENTITIES`).
"""

from __future__ import annotations


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
