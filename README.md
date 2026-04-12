# duckdb-extensions-cookbook

DuckDB extensions cookbook: extension management, spatial queries, JSON extraction, Parquet scanning, secrets, and query building.

## Patterns

- **extensions** — `ExtensionManager`, `DuckDBExtension`, dependency-ordered `load_order()`
- **query_builder** — fluent `QueryBuilder`, `CTEBuilder`, `WindowClause`
- **secret_manager** — `SecretManager` for S3/GCS/R2/Azure credentials
- **sql_patterns** — Parquet scan, JSON extract, PIVOT, TABLESAMPLE, spatial queries

## Install

```bash
pip install -e ".[dev]"
pytest
```
