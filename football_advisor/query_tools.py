from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import DuckDBConfig, load_config


class UnsafeQueryError(ValueError):
    pass


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class DuckDBStatsQueryTool:
    def __init__(
        self,
        database_path: str | None = None,
        default_limit: int = 200,
        config: DuckDBConfig | None = None,
    ) -> None:
        duckdb_config = config or load_config().duckdb
        self.database_path = database_path or duckdb_config.database_path
        self.default_limit = default_limit

    def query_match_stats(
        self, sql: str, params: list[Any] | None = None
    ) -> QueryResult:
        safe_sql = validate_select_sql(sql, self.default_limit)
        try:
            import duckdb  # type: ignore[import-not-found]
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "duckdb is not installed. Install project requirements first."
            ) from exc

        with duckdb.connect(self.database_path) as conn:
            cursor = conn.execute(safe_sql, params or [])
            columns = [desc[0] for desc in cursor.description]
            tuples = cursor.fetchall()
        rows = [dict(zip(columns, row, strict=True)) for row in tuples]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))


def validate_select_sql(sql: str, default_limit: int = 200) -> str:
    statement = sql.strip()
    if not statement:
        raise UnsafeQueryError("SQL statement is empty.")
    if "--" in statement or "/*" in statement or "*/" in statement:
        raise UnsafeQueryError("SQL comments are not allowed.")

    semicolon_stripped = statement.rstrip(";").strip()
    if ";" in semicolon_stripped:
        raise UnsafeQueryError("Only one SELECT statement is allowed.")
    if not re.match(r"(?is)^select\s+", semicolon_stripped):
        raise UnsafeQueryError("Only SELECT statements are allowed.")
    if re.search(
        r"(?is)\b(insert|update|delete|drop|alter|create|copy|attach|detach|pragma)\b",
        semicolon_stripped,
    ):
        raise UnsafeQueryError("Mutating or administrative SQL is not allowed.")
    if not re.search(r"(?is)\blimit\s+\d+\s*$", semicolon_stripped):
        semicolon_stripped = f"{semicolon_stripped} LIMIT {int(default_limit)}"
    return semicolon_stripped
