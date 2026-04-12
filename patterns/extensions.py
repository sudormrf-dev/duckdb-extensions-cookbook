"""DuckDB extension management patterns.

DuckDB extensions are loadable modules that add functionality.
Core extensions (bundled): json, parquet, httpfs, spatial, fts, tpch, etc.
Community extensions: loaded from community repository.

Patterns:
  - DuckDBExtension: metadata for a single extension
  - ExtensionSource: where the extension comes from
  - ExtensionConfig: configuration for an extension
  - ExtensionManager: tracks which extensions are loaded
  - load_order(): returns correct load order respecting dependencies

Usage::

    mgr = ExtensionManager()
    mgr.register(DuckDBExtension("spatial", requires=["httpfs"]))
    mgr.register(DuckDBExtension("httpfs"))
    ordered = load_order(mgr.all_extensions())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ExtensionSource(str, Enum):
    """Where an extension is loaded from."""

    CORE = "core"  # Bundled with DuckDB
    COMMUNITY = "community"  # From community repository
    LOCAL = "local"  # From local path
    UNSIGNED = "unsigned"  # Unsigned extension (requires allow_unsigned_extensions)


class ExtensionStatus(str, Enum):
    """Current state of an extension."""

    INSTALLED = "installed"
    LOADED = "loaded"
    NOT_INSTALLED = "not_installed"
    FAILED = "failed"


@dataclass
class ExtensionConfig:
    """Configuration for loading a DuckDB extension.

    Attributes:
        autoload: Automatically load when needed.
        autoinstall: Automatically install if missing.
        repository_url: Custom extension repository URL.
        allow_unsigned: Allow unsigned extensions.
    """

    autoload: bool = True
    autoinstall: bool = True
    repository_url: str = ""
    allow_unsigned: bool = False

    def to_settings(self) -> dict[str, str]:
        """Return DuckDB SET statements as a dict."""
        settings: dict[str, str] = {}
        if self.autoload:
            settings["autoload_known_extensions"] = "true"
        if self.autoinstall:
            settings["autoinstall_known_extensions"] = "true"
        if self.allow_unsigned:
            settings["allow_unsigned_extensions"] = "true"
        if self.repository_url:
            settings["extension_repository"] = self.repository_url
        return settings


@dataclass
class DuckDBExtension:
    """Metadata for a single DuckDB extension.

    Attributes:
        name: Extension name (e.g. "spatial", "httpfs").
        source: Where to load from.
        requires: List of extension names that must be loaded first.
        version: Optional minimum version requirement.
        description: Human-readable description.
    """

    name: str
    source: ExtensionSource = ExtensionSource.CORE
    requires: list[str] = field(default_factory=list)
    version: str = ""
    description: str = ""
    status: ExtensionStatus = ExtensionStatus.NOT_INSTALLED

    def load_sql(self) -> str:
        """Return the SQL to load this extension."""
        return f"LOAD {self.name};"

    def install_sql(self) -> str:
        """Return the SQL to install this extension."""
        return f"INSTALL {self.name};"

    def install_and_load_sql(self) -> list[str]:
        """Return install + load SQL statements."""
        return [self.install_sql(), self.load_sql()]

    def is_loaded(self) -> bool:
        """Return True if the extension is currently loaded."""
        return self.status == ExtensionStatus.LOADED

    def mark_loaded(self) -> DuckDBExtension:
        """Return self after marking as loaded (mutates in place for fluency)."""
        self.status = ExtensionStatus.LOADED
        return self


# Well-known core extensions with dependency metadata
CORE_EXTENSIONS: dict[str, DuckDBExtension] = {
    "json": DuckDBExtension("json", ExtensionSource.CORE, description="JSON functions"),
    "parquet": DuckDBExtension("parquet", ExtensionSource.CORE, description="Parquet I/O"),
    "httpfs": DuckDBExtension("httpfs", ExtensionSource.CORE, description="HTTP/S3 filesystem"),
    "spatial": DuckDBExtension(
        "spatial", ExtensionSource.CORE, requires=["httpfs"], description="Geospatial functions"
    ),
    "fts": DuckDBExtension("fts", ExtensionSource.CORE, description="Full-text search"),
    "icu": DuckDBExtension("icu", ExtensionSource.CORE, description="ICU locale support"),
    "tpch": DuckDBExtension("tpch", ExtensionSource.CORE, description="TPC-H benchmark data"),
    "tpcds": DuckDBExtension("tpcds", ExtensionSource.CORE, description="TPC-DS benchmark data"),
    "excel": DuckDBExtension("excel", ExtensionSource.CORE, description="Excel read/write"),
    "inet": DuckDBExtension("inet", ExtensionSource.CORE, description="IP address functions"),
    "aws": DuckDBExtension(
        "aws", ExtensionSource.COMMUNITY, requires=["httpfs"], description="AWS credential provider"
    ),
}


def load_order(extensions: list[DuckDBExtension]) -> list[DuckDBExtension]:
    """Return extensions sorted so dependencies come before dependents.

    Uses topological sort (Kahn's algorithm).

    Args:
        extensions: List of extensions to sort.

    Returns:
        Sorted list (dependencies first).

    Raises:
        ValueError: If a circular dependency is detected.
    """
    name_to_ext: dict[str, DuckDBExtension] = {e.name: e for e in extensions}
    in_degree: dict[str, int] = {e.name: 0 for e in extensions}
    dependents: dict[str, list[str]] = {e.name: [] for e in extensions}

    for ext in extensions:
        for dep in ext.requires:
            if dep in name_to_ext:
                in_degree[ext.name] += 1
                dependents[dep].append(ext.name)

    queue = [name for name, deg in in_degree.items() if deg == 0]
    queue.sort()  # deterministic order
    result: list[DuckDBExtension] = []

    while queue:
        current = queue.pop(0)
        result.append(name_to_ext[current])
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(extensions):
        err = "Circular dependency detected in extensions"
        raise ValueError(err)

    return result


class ExtensionManager:
    """Tracks registered DuckDB extensions and their states.

    Args:
        config: Global extension configuration.
    """

    def __init__(self, config: ExtensionConfig | None = None) -> None:
        self._config = config or ExtensionConfig()
        self._extensions: dict[str, DuckDBExtension] = {}

    @property
    def config(self) -> ExtensionConfig:
        """The global extension configuration."""
        return self._config

    def register(self, extension: DuckDBExtension) -> ExtensionManager:
        """Register an extension."""
        self._extensions[extension.name] = extension
        return self

    def register_core(self, name: str) -> ExtensionManager:
        """Register a well-known core extension by name."""
        if name in CORE_EXTENSIONS:
            self._extensions[name] = CORE_EXTENSIONS[name]
        else:
            self._extensions[name] = DuckDBExtension(name, ExtensionSource.CORE)
        return self

    def get(self, name: str) -> DuckDBExtension | None:
        """Return an extension by name."""
        return self._extensions.get(name)

    def all_extensions(self) -> list[DuckDBExtension]:
        """Return all registered extensions."""
        return list(self._extensions.values())

    def loaded_extensions(self) -> list[str]:
        """Return names of loaded extensions."""
        return [e.name for e in self._extensions.values() if e.is_loaded()]

    def load_sql_script(self) -> str:
        """Return SQL script to install and load all extensions in dependency order."""
        ordered = load_order(self.all_extensions())
        lines: list[str] = []
        for ext in ordered:
            lines.extend(ext.install_and_load_sql())
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._extensions)
