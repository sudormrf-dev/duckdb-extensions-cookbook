"""Tests for sql_patterns.py."""

from __future__ import annotations

from patterns.sql_patterns import (
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


class TestParquetScanConfig:
    def test_has_s3_path_true(self):
        cfg = ParquetScanConfig(path="s3://my-bucket/data.parquet")
        assert cfg.has_s3_path() is True

    def test_has_s3_path_false(self):
        cfg = ParquetScanConfig(path="/local/path.parquet")
        assert cfg.has_s3_path() is False

    def test_has_glob_true(self):
        cfg = ParquetScanConfig(path="data/**/*.parquet")
        assert cfg.has_glob() is True

    def test_has_glob_false(self):
        cfg = ParquetScanConfig(path="data/file.parquet")
        assert cfg.has_glob() is False


class TestBuildParquetScan:
    def test_basic(self):
        cfg = ParquetScanConfig(path="data.parquet")
        sql = build_parquet_scan(cfg)
        assert "read_parquet" in sql
        assert "data.parquet" in sql

    def test_hive_partitioning(self):
        cfg = ParquetScanConfig(path="data/**", hive_partitioning=True)
        assert "hive_partitioning=true" in build_parquet_scan(cfg)

    def test_union_by_name(self):
        cfg = ParquetScanConfig(path="data/**", union_by_name=True)
        assert "union_by_name=true" in build_parquet_scan(cfg)

    def test_filename(self):
        cfg = ParquetScanConfig(path="data/**", filename=True)
        assert "filename=true" in build_parquet_scan(cfg)

    def test_file_row_number(self):
        cfg = ParquetScanConfig(path="data/**", file_row_number=True)
        assert "file_row_number=true" in build_parquet_scan(cfg)

    def test_binary_as_string(self):
        cfg = ParquetScanConfig(path="data.parquet", binary_as_string=True)
        assert "binary_as_string=true" in build_parquet_scan(cfg)

    def test_column_projection(self):
        cfg = ParquetScanConfig(path="data.parquet", columns=["id", "name"])
        assert "columns=" in build_parquet_scan(cfg)

    def test_filters(self):
        cfg = ParquetScanConfig(path="data.parquet", filters=["amount > 100"])
        sql = build_parquet_scan(cfg)
        assert "WHERE" in sql
        assert "amount > 100" in sql

    def test_multiple_filters(self):
        cfg = ParquetScanConfig(path="data.parquet", filters=["a > 0", "b < 10"])
        sql = build_parquet_scan(cfg)
        assert "AND" in sql


class TestBuildJsonExtract:
    def test_simple_field(self):
        expr = build_json_extract("meta", "$.name")
        assert "meta" in expr
        assert "name" in expr

    def test_as_text(self):
        expr = build_json_extract("meta", "$.name", as_text=True)
        assert "->>" in expr

    def test_json_extraction(self):
        expr = build_json_extract("meta", "$.age")
        assert "->" in expr

    def test_alias(self):
        expr = build_json_extract("meta", "$.name", alias="user_name")
        assert "AS user_name" in expr

    def test_non_dollar_path(self):
        expr = build_json_extract("col", "key")
        assert "json_extract" in expr

    def test_as_text_non_dollar(self):
        expr = build_json_extract("col", "key", as_text=True)
        assert "json_extract_string" in expr


class TestSampleConfig:
    def test_percentage_bernoulli(self):
        cfg = SampleConfig(method=SampleMethod.BERNOULLI, size=10.0)
        assert cfg.is_percentage() is True

    def test_percentage_system(self):
        cfg = SampleConfig(method=SampleMethod.SYSTEM, size=5.0)
        assert cfg.is_percentage() is True

    def test_not_percentage_reservoir(self):
        cfg = SampleConfig(method=SampleMethod.RESERVOIR, size=1000)
        assert cfg.is_percentage() is False


class TestBuildSample:
    def test_bernoulli(self):
        cfg = SampleConfig(method=SampleMethod.BERNOULLI, size=10.0)
        sql = build_sample("orders", cfg)
        assert "USING SAMPLE bernoulli" in sql
        assert "10.0%" in sql

    def test_reservoir(self):
        cfg = SampleConfig(method=SampleMethod.RESERVOIR, size=1000)
        sql = build_sample("orders", cfg)
        assert "reservoir" in sql
        assert "1000" in sql
        assert "%" not in sql

    def test_seed(self):
        cfg = SampleConfig(seed=42)
        sql = build_sample("t", cfg)
        assert "REPEATABLE (42)" in sql

    def test_no_seed(self):
        cfg = SampleConfig()
        sql = build_sample("t", cfg)
        assert "REPEATABLE" not in sql


class TestBuildPivot:
    def test_basic(self):
        spec = PivotSpec(table="sales", on="quarter", using="SUM(amount)")
        sql = build_pivot(spec)
        assert "PIVOT sales" in sql
        assert "ON quarter" in sql
        assert "USING SUM(amount)" in sql

    def test_group_by(self):
        spec = PivotSpec(table="sales", on="q", using="SUM(v)", group_by=["region"])
        sql = build_pivot(spec)
        assert "GROUP BY region" in sql

    def test_in_values(self):
        spec = PivotSpec(table="t", on="q", using="SUM(v)", in_values=["Q1", "Q2"])
        sql = build_pivot(spec)
        assert "IN (" in sql
        assert "Q1" in sql


class TestBuildSpatialQuery:
    def test_within(self):
        sql = build_spatial_query("t", "geom", "within", wkt="POLYGON((...))")
        assert "ST_Within" in sql

    def test_intersects(self):
        sql = build_spatial_query("t", "geom", "intersects", wkt="POLYGON((...))")
        assert "ST_Intersects" in sql

    def test_buffer(self):
        sql = build_spatial_query("t", "geom", "buffer", distance=100.0)
        assert "ST_Buffer" in sql
        assert "100.0" in sql

    def test_transform(self):
        sql = build_spatial_query("t", "geom", "transform", target_srid=4326)
        assert "ST_Transform" in sql
        assert "4326" in sql

    def test_area(self):
        sql = build_spatial_query("t", "geom", "area")
        assert "ST_Area" in sql

    def test_distance(self):
        sql = build_spatial_query("t", "geom", "distance", wkt="POINT(0 0)")
        assert "ST_Distance" in sql

    def test_unknown_operation_fallback(self):
        sql = build_spatial_query("t", "geom", "unknown_op")
        assert "geom" in sql
        assert "FROM t" in sql
