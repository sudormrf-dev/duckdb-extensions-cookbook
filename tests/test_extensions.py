"""Tests for extensions.py."""

from __future__ import annotations

import pytest

from patterns.extensions import (
    CORE_EXTENSIONS,
    DuckDBExtension,
    ExtensionConfig,
    ExtensionManager,
    ExtensionSource,
    ExtensionStatus,
    load_order,
)


class TestExtensionConfig:
    def test_defaults(self):
        cfg = ExtensionConfig()
        assert cfg.autoload is True
        assert cfg.allow_unsigned is False

    def test_to_settings_autoload(self):
        cfg = ExtensionConfig(autoload=True)
        s = cfg.to_settings()
        assert s["autoload_known_extensions"] == "true"

    def test_to_settings_unsigned(self):
        cfg = ExtensionConfig(allow_unsigned=True)
        assert "allow_unsigned_extensions" in cfg.to_settings()

    def test_to_settings_repository(self):
        cfg = ExtensionConfig(repository_url="https://my.repo/")
        assert "extension_repository" in cfg.to_settings()

    def test_to_settings_empty_when_all_false(self):
        cfg = ExtensionConfig(autoload=False, autoinstall=False, allow_unsigned=False)
        s = cfg.to_settings()
        assert "autoload_known_extensions" not in s


class TestDuckDBExtension:
    def test_load_sql(self):
        ext = DuckDBExtension("spatial")
        assert ext.load_sql() == "LOAD spatial;"

    def test_install_sql(self):
        ext = DuckDBExtension("spatial")
        assert ext.install_sql() == "INSTALL spatial;"

    def test_install_and_load(self):
        ext = DuckDBExtension("spatial")
        stmts = ext.install_and_load_sql()
        assert len(stmts) == 2

    def test_is_loaded_false(self):
        ext = DuckDBExtension("spatial")
        assert ext.is_loaded() is False

    def test_mark_loaded(self):
        ext = DuckDBExtension("spatial")
        ext.mark_loaded()
        assert ext.is_loaded() is True

    def test_mark_loaded_returns_self(self):
        ext = DuckDBExtension("spatial")
        assert ext.mark_loaded() is ext


class TestLoadOrder:
    def test_no_deps(self):
        exts = [DuckDBExtension("json"), DuckDBExtension("parquet")]
        ordered = load_order(exts)
        assert len(ordered) == 2

    def test_dependency_first(self):
        httpfs = DuckDBExtension("httpfs")
        spatial = DuckDBExtension("spatial", requires=["httpfs"])
        ordered = load_order([spatial, httpfs])
        names = [e.name for e in ordered]
        assert names.index("httpfs") < names.index("spatial")

    def test_chain_dependency(self):
        a = DuckDBExtension("a")
        b = DuckDBExtension("b", requires=["a"])
        c = DuckDBExtension("c", requires=["b"])
        ordered = load_order([c, b, a])
        names = [e.name for e in ordered]
        assert names.index("a") < names.index("b") < names.index("c")

    def test_external_deps_ignored(self):
        # If required extension is not in the list, it's treated as external
        spatial = DuckDBExtension("spatial", requires=["httpfs"])
        ordered = load_order([spatial])  # httpfs not in list
        assert len(ordered) == 1

    def test_circular_raises(self):
        a = DuckDBExtension("a", requires=["b"])
        b = DuckDBExtension("b", requires=["a"])
        with pytest.raises(ValueError):
            load_order([a, b])

    def test_empty_list(self):
        assert load_order([]) == []


class TestExtensionManager:
    def test_register_and_len(self):
        mgr = ExtensionManager()
        mgr.register(DuckDBExtension("json"))
        assert len(mgr) == 1

    def test_get_existing(self):
        mgr = ExtensionManager()
        ext = DuckDBExtension("json")
        mgr.register(ext)
        assert mgr.get("json") is ext

    def test_get_missing(self):
        mgr = ExtensionManager()
        assert mgr.get("missing") is None

    def test_register_core(self):
        mgr = ExtensionManager()
        mgr.register_core("spatial")
        assert mgr.get("spatial") is not None

    def test_register_unknown_core(self):
        mgr = ExtensionManager()
        mgr.register_core("unknown_ext")
        assert mgr.get("unknown_ext") is not None

    def test_loaded_extensions_empty(self):
        mgr = ExtensionManager()
        mgr.register(DuckDBExtension("json"))
        assert mgr.loaded_extensions() == []

    def test_loaded_extensions_after_mark(self):
        mgr = ExtensionManager()
        ext = DuckDBExtension("json")
        mgr.register(ext)
        ext.mark_loaded()
        assert "json" in mgr.loaded_extensions()

    def test_load_sql_script(self):
        mgr = ExtensionManager()
        mgr.register(DuckDBExtension("httpfs"))
        mgr.register(DuckDBExtension("spatial", requires=["httpfs"]))
        script = mgr.load_sql_script()
        assert "INSTALL httpfs" in script
        assert "LOAD spatial" in script

    def test_chaining(self):
        mgr = (
            ExtensionManager()
            .register(DuckDBExtension("json"))
            .register(DuckDBExtension("parquet"))
        )
        assert len(mgr) == 2

    def test_core_extensions_dict(self):
        assert "spatial" in CORE_EXTENSIONS
        assert CORE_EXTENSIONS["spatial"].requires == ["httpfs"]

    def test_extension_source_community(self):
        ext = DuckDBExtension("custom", source=ExtensionSource.COMMUNITY)
        assert ext.source == ExtensionSource.COMMUNITY

    def test_extension_status_default(self):
        ext = DuckDBExtension("json")
        assert ext.status == ExtensionStatus.NOT_INSTALLED
