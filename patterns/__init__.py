"""DuckDB extensions cookbook: spatial, JSON, Parquet, httpfs, secrets."""

from .extensions import (
    DuckDBExtension,
    ExtensionConfig,
    ExtensionManager,
    ExtensionSource,
    load_order,
)
from .query_builder import (
    CTEBuilder,
    JoinType,
    OrderDirection,
    QueryBuilder,
    WindowClause,
)
from .secret_manager import (
    ProviderType,
    SecretConfig,
    SecretManager,
    SecretScope,
    SecretType,
)
from .sql_patterns import (
    ParquetScanConfig,
    PivotSpec,
    SampleConfig,
    SampleMethod,
    build_json_extract,
    build_parquet_scan,
    build_pivot,
    build_sample,
    build_spatial_query,
)

__all__ = [
    "CTEBuilder",
    "DuckDBExtension",
    "ExtensionConfig",
    "ExtensionManager",
    "ExtensionSource",
    "JoinType",
    "OrderDirection",
    "ParquetScanConfig",
    "PivotSpec",
    "ProviderType",
    "QueryBuilder",
    "SampleConfig",
    "SampleMethod",
    "SecretConfig",
    "SecretManager",
    "SecretScope",
    "SecretType",
    "WindowClause",
    "build_json_extract",
    "build_parquet_scan",
    "build_pivot",
    "build_sample",
    "build_spatial_query",
    "load_order",
]
