"""DuckDB SQL query builder patterns.

DuckDB supports standard SQL plus many analytical extensions:
PIVOT/UNPIVOT, ASOF JOIN, window functions, LIST aggregations, QUALIFY, etc.

Patterns:
  - QueryBuilder: fluent SELECT builder
  - CTEBuilder: WITH clause (common table expressions) builder
  - WindowClause: OVER() clause builder
  - JoinType: join type enum
  - OrderDirection: ASC/DESC enum

Usage::

    q = (
        QueryBuilder("orders")
        .select("customer_id", "SUM(amount) AS total")
        .where("amount > 0")
        .group_by("customer_id")
        .order_by("total", OrderDirection.DESC)
        .limit(10)
    )
    sql = q.build()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class JoinType(str, Enum):
    """SQL JOIN types supported by DuckDB."""

    INNER = "INNER JOIN"
    LEFT = "LEFT JOIN"
    RIGHT = "RIGHT JOIN"
    FULL = "FULL OUTER JOIN"
    CROSS = "CROSS JOIN"
    ASOF = "ASOF JOIN"  # DuckDB-specific time-series join
    POSITIONAL = "POSITIONAL JOIN"  # DuckDB-specific positional join


class OrderDirection(str, Enum):
    """Sort direction."""

    ASC = "ASC"
    DESC = "DESC"


@dataclass
class WindowClause:
    """Represents an OVER() window specification.

    Attributes:
        partition_by: Columns to partition by.
        order_by: Columns to order by within the window.
        frame: Optional frame specification string (e.g. "ROWS BETWEEN ...").
        direction: Sort direction for order_by columns.
    """

    partition_by: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    frame: str = ""
    direction: OrderDirection = OrderDirection.ASC

    def to_sql(self) -> str:
        """Render the OVER() clause."""
        parts: list[str] = []
        if self.partition_by:
            parts.append("PARTITION BY " + ", ".join(self.partition_by))
        if self.order_by:
            cols = ", ".join(f"{c} {self.direction.value}" for c in self.order_by)
            parts.append(f"ORDER BY {cols}")
        if self.frame:
            parts.append(self.frame)
        return "OVER (" + " ".join(parts) + ")"

    def with_frame(self, frame: str) -> WindowClause:
        """Return a copy with a different frame specification."""
        return WindowClause(
            partition_by=list(self.partition_by),
            order_by=list(self.order_by),
            frame=frame,
            direction=self.direction,
        )


class QueryBuilder:
    """Fluent DuckDB SELECT query builder.

    Args:
        table: Primary table or subquery (can use aliases: "orders o").
    """

    def __init__(self, table: str = "") -> None:
        self._table = table
        self._selects: list[str] = []
        self._joins: list[str] = []
        self._wheres: list[str] = []
        self._group_bys: list[str] = []
        self._havings: list[str] = []
        self._order_bys: list[str] = []
        self._limit_val: int | None = None
        self._offset_val: int | None = None
        self._qualify: str = ""
        self._distinct: bool = False

    def select(self, *columns: str) -> QueryBuilder:
        """Add SELECT columns."""
        self._selects.extend(columns)
        return self

    def distinct(self) -> QueryBuilder:
        """Add DISTINCT modifier."""
        self._distinct = True
        return self

    def join(
        self,
        table: str,
        condition: str,
        join_type: JoinType = JoinType.INNER,
    ) -> QueryBuilder:
        """Add a JOIN clause."""
        self._joins.append(f"{join_type.value} {table} ON {condition}")
        return self

    def where(self, *conditions: str) -> QueryBuilder:
        """Add WHERE conditions (ANDed together)."""
        self._wheres.extend(conditions)
        return self

    def group_by(self, *columns: str) -> QueryBuilder:
        """Add GROUP BY columns."""
        self._group_bys.extend(columns)
        return self

    def having(self, condition: str) -> QueryBuilder:
        """Add HAVING condition."""
        self._havings.append(condition)
        return self

    def order_by(
        self,
        column: str,
        direction: OrderDirection = OrderDirection.ASC,
    ) -> QueryBuilder:
        """Add ORDER BY column."""
        self._order_bys.append(f"{column} {direction.value}")
        return self

    def limit(self, n: int) -> QueryBuilder:
        """Set LIMIT."""
        self._limit_val = n
        return self

    def offset(self, n: int) -> QueryBuilder:
        """Set OFFSET."""
        self._offset_val = n
        return self

    def qualify(self, condition: str) -> QueryBuilder:
        """Set QUALIFY clause (DuckDB window function filter)."""
        self._qualify = condition
        return self

    def build(self) -> str:
        """Render the full SELECT statement."""
        distinct_kw = "DISTINCT " if self._distinct else ""
        cols = ", ".join(self._selects) if self._selects else "*"
        sql = f"SELECT {distinct_kw}{cols}"
        if self._table:
            sql += f"\nFROM {self._table}"
        for j in self._joins:
            sql += f"\n{j}"
        if self._wheres:
            sql += "\nWHERE " + "\n  AND ".join(self._wheres)
        if self._group_bys:
            sql += "\nGROUP BY " + ", ".join(self._group_bys)
        if self._havings:
            sql += "\nHAVING " + " AND ".join(self._havings)
        if self._qualify:
            sql += f"\nQUALIFY {self._qualify}"
        if self._order_bys:
            sql += "\nORDER BY " + ", ".join(self._order_bys)
        if self._limit_val is not None:
            sql += f"\nLIMIT {self._limit_val}"
        if self._offset_val is not None:
            sql += f"\nOFFSET {self._offset_val}"
        return sql

    def count(self) -> str:
        """Return a COUNT(*) wrapper around this query."""
        inner = self.build()
        return f"SELECT COUNT(*) FROM ({inner}) _count"


class CTEBuilder:
    """Builder for SQL WITH (CTE) queries.

    Usage::

        cte = (
            CTEBuilder()
            .with_cte("ranked", "SELECT *, ROW_NUMBER() OVER () AS rn FROM events")
            .with_cte("top10", "SELECT * FROM ranked WHERE rn <= 10")
        )
        sql = cte.build("SELECT * FROM top10")
    """

    def __init__(self) -> None:
        self._ctes: list[tuple[str, str]] = []
        self._recursive: bool = False

    def with_cte(self, name: str, query: str) -> CTEBuilder:
        """Add a CTE definition."""
        self._ctes.append((name, query))
        return self

    def recursive(self) -> CTEBuilder:
        """Mark this CTE as RECURSIVE."""
        self._recursive = True
        return self

    def build(self, final_query: str) -> str:
        """Build the full WITH ... SELECT statement.

        Args:
            final_query: The main SELECT that follows the CTEs.

        Returns:
            Complete SQL string.
        """
        if not self._ctes:
            return final_query
        recursive_kw = " RECURSIVE" if self._recursive else ""
        cte_parts = ",\n".join(f"{name} AS (\n{query}\n)" for name, query in self._ctes)
        return f"WITH{recursive_kw}\n{cte_parts}\n{final_query}"

    def cte_names(self) -> list[str]:
        """Return names of all registered CTEs."""
        return [name for name, _ in self._ctes]

    def __len__(self) -> int:
        return len(self._ctes)
