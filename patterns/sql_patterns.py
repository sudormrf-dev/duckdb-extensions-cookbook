"""DuckDB-specific SQL pattern builders.

Patterns for DuckDB analytical extensions:
  - Parquet scan with hive partitioning
  - JSON extraction paths
  - Spatial query helpers (requires spatial extension)
  - PIVOT/UNPIVOT
  - TABLESAMPLE / USING SAMPLE

Usage::

    sql = build_parquet_scan(ParquetScanConfig(
        path="s3://bucket/data/**/*.parquet",
        hive_partitioning=True,
    ))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SampleMethod(str, Enum):
    """DuckDB TABLESAMPLE methods."""

    BERNOULLI = "bernoulli"  # Random row selection (percentage)
    SYSTEM = "system"  # Block-level random sampling (percentage)
    RESERVOIR = "reservoir"  # Fixed-size reservoir sampling


@dataclass
class ParquetScanConfig:
    """Configuration for a DuckDB Parquet scan.

    Attributes:
        path: File path or glob pattern (supports s3://, http://, local).
        hive_partitioning: Parse directory names as partition columns.
        columns: Optional column projection list.
        filters: Optional pushed-down filter expressions.
        union_by_name: Union files by column name instead of position.
        filename: Include a 'filename' column.
        file_row_number: Include row number within each file.
        binary_as_string: Read BYTE_ARRAY as VARCHAR.
    """

    path: str = ""
    hive_partitioning: bool = False
    columns: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    union_by_name: bool = False
    filename: bool = False
    file_row_number: bool = False
    binary_as_string: bool = False

    def has_s3_path(self) -> bool:
        """Return True if path points to S3."""
        return self.path.startswith("s3://")

    def has_glob(self) -> bool:
        """Return True if path contains a glob pattern."""
        return "*" in self.path or "?" in self.path


def build_parquet_scan(config: ParquetScanConfig) -> str:
    """Build a DuckDB read_parquet() call.

    Args:
        config: Parquet scan configuration.

    Returns:
        SQL expression string for read_parquet().
    """
    params: list[str] = [f"'{config.path}'"]

    if config.hive_partitioning:
        params.append("hive_partitioning=true")
    if config.union_by_name:
        params.append("union_by_name=true")
    if config.filename:
        params.append("filename=true")
    if config.file_row_number:
        params.append("file_row_number=true")
    if config.binary_as_string:
        params.append("binary_as_string=true")
    if config.columns:
        col_list = "[" + ", ".join(f"'{c}'" for c in config.columns) + "]"
        params.append(f"columns={col_list}")

    param_str = ", ".join(params)
    base = f"read_parquet({param_str})"

    if config.filters:
        filter_expr = " AND ".join(config.filters)
        return f"SELECT * FROM {base} WHERE {filter_expr}"  # nosec B608

    return f"SELECT * FROM {base}"  # nosec B608


def build_json_extract(
    column: str,
    path: str,
    alias: str = "",
    as_text: bool = False,
) -> str:
    """Build a DuckDB JSON extraction expression.

    DuckDB supports both JSONPath (json_extract) and arrow-style (column->>'$.path').

    Args:
        column: Column containing JSON.
        path: JSONPath expression (e.g. "$.user.name").
        alias: Optional alias for the expression.
        as_text: Use ->> (text extraction) instead of -> (JSON extraction).

    Returns:
        SQL expression string.
    """
    op = "->>" if as_text else "->"
    # Simplify $.field to 'field' for arrow operator
    if path.startswith("$."):
        field_path = path[2:]  # strip "$."
        expr = f"{column}{op}'{field_path}'"
    else:
        fn = "json_extract_string" if as_text else "json_extract"
        expr = f"{fn}({column}, '{path}')"

    if alias:
        return f"{expr} AS {alias}"
    return expr


@dataclass
class SampleConfig:
    """Configuration for DuckDB TABLESAMPLE / USING SAMPLE.

    Attributes:
        method: Sampling method.
        size: Sample size (percentage for BERNOULLI/SYSTEM, rows for RESERVOIR).
        seed: Optional random seed for reproducibility.
    """

    method: SampleMethod = SampleMethod.BERNOULLI
    size: float = 10.0
    seed: int | None = None

    def is_percentage(self) -> bool:
        """Return True if size is a percentage (BERNOULLI or SYSTEM)."""
        return self.method in {SampleMethod.BERNOULLI, SampleMethod.SYSTEM}


def build_sample(table: str, config: SampleConfig) -> str:
    """Build a DuckDB USING SAMPLE query.

    Args:
        table: Table name or subquery.
        config: Sample configuration.

    Returns:
        SQL string using TABLESAMPLE or USING SAMPLE.
    """
    size_str = f"{config.size}%" if config.is_percentage() else str(int(config.size))
    seed_str = f" REPEATABLE ({config.seed})" if config.seed is not None else ""
    return f"SELECT * FROM {table} USING SAMPLE {config.method.value}({size_str}){seed_str}"  # nosec B608


@dataclass
class PivotSpec:
    """Specification for a DuckDB PIVOT query.

    Attributes:
        table: Source table or subquery.
        on: Column whose distinct values become new column headers.
        using: Aggregation expression (e.g. "SUM(amount)").
        group_by: Columns to group by (become row identifiers).
        in_values: Optional explicit list of pivot values.
    """

    table: str
    on: str
    using: str
    group_by: list[str] = field(default_factory=list)
    in_values: list[Any] = field(default_factory=list)


def build_pivot(spec: PivotSpec) -> str:
    """Build a DuckDB PIVOT statement.

    Args:
        spec: Pivot specification.

    Returns:
        PIVOT SQL string.
    """
    group_str = ""
    if spec.group_by:
        group_str = "\nGROUP BY " + ", ".join(spec.group_by)

    in_str = ""
    if spec.in_values:
        vals = ", ".join(repr(v) for v in spec.in_values)
        in_str = f" IN ({vals})"

    return f"PIVOT {spec.table}\nON {spec.on}{in_str}\nUSING {spec.using}{group_str}"


def build_spatial_query(
    table: str,
    geom_column: str,
    operation: str,
    *,
    wkt: str = "",
    distance: float | None = None,
    target_srid: int | None = None,
) -> str:
    """Build a DuckDB spatial extension query.

    Requires the ``spatial`` extension to be loaded.

    Args:
        table: Source table.
        geom_column: Geometry column name.
        operation: Spatial operation ("within", "intersects", "buffer",
            "transform", "area", "distance").
        wkt: WKT geometry string for filter operations.
        distance: Distance for buffer operation.
        target_srid: Target SRID for transform operation.

    Returns:
        SQL expression string.
    """
    if operation == "within" and wkt:
        return f"SELECT * FROM {table} WHERE ST_Within({geom_column}, ST_GeomFromText('{wkt}'))"  # nosec B608
    if operation == "intersects" and wkt:
        return f"SELECT * FROM {table} WHERE ST_Intersects({geom_column}, ST_GeomFromText('{wkt}'))"  # nosec B608
    if operation == "buffer" and distance is not None:
        return f"SELECT ST_Buffer({geom_column}, {distance}) AS buffered FROM {table}"  # nosec B608
    if operation == "transform" and target_srid is not None:
        return (
            f"SELECT ST_Transform({geom_column}, 'EPSG:{target_srid}') AS transformed FROM {table}"  # nosec B608
        )
    if operation == "area":
        return f"SELECT ST_Area({geom_column}) AS area FROM {table}"  # nosec B608
    if operation == "distance" and wkt:
        return f"SELECT ST_Distance({geom_column}, ST_GeomFromText('{wkt}')) AS dist FROM {table}"  # nosec B608
    return f"SELECT {geom_column} FROM {table}"  # nosec B608
