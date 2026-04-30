"""Financial ETL example: OHLCV stock data pipeline with DuckDB SQL patterns.

Demonstrates:
  - Generating 1,000 synthetic trades (OHLCV stock data) in memory
  - Using sql_patterns.py builders: ParquetScanConfig, build_json_extract, build_pivot
  - ETL pipeline: scan CSV-like data → transform → aggregate → export summary
  - Running via DuckDB if available, otherwise simulating with stdlib

Usage::

    python examples/financial_etl.py
"""

from __future__ import annotations

import csv
import io
import json
import random
import time
from datetime import date, timedelta
from typing import Any

from patterns.query_builder import CTEBuilder, OrderDirection, QueryBuilder
from patterns.sql_patterns import (
    ParquetScanConfig,
    PivotSpec,
    build_json_extract,
    build_parquet_scan,
    build_pivot,
)

# ---------------------------------------------------------------------------
# Synthetic OHLCV data generation
# ---------------------------------------------------------------------------

TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX"]
SECTORS = {
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "technology",
    "AMZN": "consumer",
    "NVDA": "technology",
    "TSLA": "automotive",
    "META": "technology",
    "NFLX": "media",
}

BASE_PRICES = {
    "AAPL": 185.0,
    "MSFT": 375.0,
    "GOOGL": 140.0,
    "AMZN": 178.0,
    "NVDA": 620.0,
    "TSLA": 240.0,
    "META": 490.0,
    "NFLX": 610.0,
}


def generate_ohlcv(n_trades: int = 1000, seed: int = 99) -> list[dict]:  # type: ignore[type-arg]
    """Generate *n_trades* synthetic OHLCV records.

    Each record represents one daily candle for a random ticker.

    Args:
        n_trades: Total number of records.
        seed: Random seed for reproducibility.

    Returns:
        List of dicts with keys: date, ticker, sector, open, high, low, close, volume, vwap.
    """
    rng = random.Random(seed)
    prices = dict(BASE_PRICES)
    records = []
    start = date(2024, 1, 2)

    for i in range(n_trades):
        ticker = rng.choice(TICKERS)
        pct_change = rng.gauss(0.0003, 0.018)
        prices[ticker] = max(prices[ticker] * (1 + pct_change), 1.0)

        close = round(prices[ticker], 2)
        open_ = round(close * (1 + rng.gauss(0, 0.005)), 2)
        high = round(max(open_, close) * (1 + abs(rng.gauss(0, 0.008))), 2)
        low = round(min(open_, close) * (1 - abs(rng.gauss(0, 0.008))), 2)
        volume = rng.randint(500_000, 50_000_000)
        vwap = round((high + low + close) / 3, 2)

        trade_date = start + timedelta(days=i // len(TICKERS))
        records.append(
            {
                "date": trade_date.isoformat(),
                "ticker": ticker,
                "sector": SECTORS[ticker],
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "vwap": vwap,
                "metadata": json.dumps({"source": "synthetic", "version": "1.0", "seed": seed}),
            }
        )

    return records


def records_to_csv(records: list[dict]) -> str:  # type: ignore[type-arg]
    """Serialize records to a CSV string (in-memory).

    Args:
        records: List of trade dicts.

    Returns:
        CSV-formatted string.
    """
    if not records:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# SQL builders — showcasing sql_patterns.py
# ---------------------------------------------------------------------------


def demo_parquet_scan_sql() -> str:
    """Show how build_parquet_scan generates a read_parquet() call.

    Returns:
        SQL string for scanning partitioned Parquet files from S3.
    """
    cfg = ParquetScanConfig(
        path="s3://my-data-lake/market/ohlcv/**/*.parquet",
        hive_partitioning=True,
        columns=["date", "ticker", "close", "volume"],
        filters=["ticker IN ('AAPL', 'MSFT', 'NVDA')", "date >= '2024-01-01'"],
        union_by_name=True,
    )
    return build_parquet_scan(cfg)


def demo_json_extract_sql() -> str:
    """Show how build_json_extract creates a JSON path expression.

    Returns:
        SQL expression string.
    """
    return build_json_extract("metadata", "$.source", alias="data_source", as_text=True)


def build_sector_pivot_sql(table: str) -> str:
    """Build a PIVOT showing avg close price per sector per month.

    Args:
        table: Source table name.

    Returns:
        PIVOT SQL string.
    """
    spec = PivotSpec(
        table=f"(SELECT strftime(date, '%Y-%m') AS month, sector, close FROM {table})",
        on="sector",
        using="ROUND(AVG(close), 2)",
        group_by=["month"],
        in_values=["technology", "consumer", "automotive", "media"],
    )
    return build_pivot(spec)


def build_returns_cte_sql(table: str) -> str:
    """Build a CTE computing daily returns and 5-day rolling avg.

    Args:
        table: Source table with OHLCV data.

    Returns:
        Full WITH ... SELECT SQL string.
    """
    base = (
        QueryBuilder(table)
        .select(
            "date",
            "ticker",
            "sector",
            "close",
            "LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS prev_close",
        )
        .build()
    )

    returns = (
        QueryBuilder("base")
        .select(
            "date",
            "ticker",
            "sector",
            "close",
            "ROUND(100.0 * (close - prev_close) / NULLIF(prev_close, 0), 4) AS daily_return_pct",
        )
        .where("prev_close IS NOT NULL")
        .build()
    )

    summary = (
        QueryBuilder("returns")
        .select(
            "ticker",
            "sector",
            "COUNT(*) AS trading_days",
            "ROUND(AVG(daily_return_pct), 4) AS avg_daily_return",
            "ROUND(STDDEV(daily_return_pct), 4) AS volatility",
            "ROUND(MIN(daily_return_pct), 4) AS worst_day",
            "ROUND(MAX(daily_return_pct), 4) AS best_day",
        )
        .group_by("ticker", "sector")
        .order_by("avg_daily_return", OrderDirection.DESC)
        .build()
    )

    return CTEBuilder().with_cte("base", base).with_cte("returns", returns).build(summary)


# ---------------------------------------------------------------------------
# Stdlib simulation
# ---------------------------------------------------------------------------


def _simulate_sector_avg(records: list[dict]) -> dict[str, dict[str, float]]:  # type: ignore[type-arg]
    """Compute avg close per sector using stdlib.

    Args:
        records: List of trade dicts.

    Returns:
        Dict mapping sector -> avg_close.
    """
    totals: dict[str, list[float]] = {}
    for r in records:
        sector = r["sector"]
        if sector not in totals:
            totals[sector] = []
        totals[sector].append(float(r["close"]))
    return {s: {"avg_close": round(sum(v) / len(v), 2), "count": len(v)} for s, v in totals.items()}


def _simulate_ticker_returns(records: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    """Compute per-ticker return stats using stdlib.

    Args:
        records: List of trade dicts.

    Returns:
        List of dicts with ticker return statistics.
    """
    import statistics

    by_ticker: dict[str, list[float]] = {}
    for r in records:
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = []
        by_ticker[t].append(float(r["close"]))

    results = []
    for ticker, closes in by_ticker.items():
        if len(closes) < 2:
            continue
        returns = [
            100.0 * (closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))
        ]
        results.append(
            {
                "ticker": ticker,
                "sector": SECTORS[ticker],
                "trading_days": len(returns),
                "avg_daily_return": round(statistics.mean(returns), 4),
                "volatility": round(statistics.stdev(returns), 4) if len(returns) > 1 else 0.0,
                "worst_day": round(min(returns), 4),
                "best_day": round(max(returns), 4),
            }
        )
    results.sort(key=lambda r: r["avg_daily_return"], reverse=True)  # type: ignore[arg-type,return-value]
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_table(headers: list[str], rows: list[Any], limit: int | None = None) -> None:
    """Print a simple aligned table.

    Args:
        headers: Column header names.
        rows: Row data (list of lists/tuples).
        limit: Max rows to display.
    """
    display = list(rows)[:limit] if limit else list(rows)
    col_w = [len(h) for h in headers]
    for row in display:
        for i, cell in enumerate(row):
            col_w[i] = max(col_w[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_w))
    for row in display:
        print(fmt.format(*[str(c) for c in row]))


def main() -> None:
    """Run the financial ETL demo."""
    t0 = time.perf_counter()
    print("=== Financial ETL — OHLCV Pipeline ===\n")

    print("Generating 1,000 synthetic trades...")
    records = generate_ohlcv(1000)
    csv_str = records_to_csv(records)
    gen_ms = (time.perf_counter() - t0) * 1000
    print(f"Generated {len(records):,} records ({len(csv_str):,} CSV bytes) in {gen_ms:.1f} ms\n")

    # Show SQL patterns from sql_patterns.py
    print("--- Parquet Scan SQL (build_parquet_scan) ---")
    print(
        build_parquet_scan(
            ParquetScanConfig(
                path="s3://my-data-lake/market/ohlcv/**/*.parquet",
                hive_partitioning=True,
                columns=["date", "ticker", "close", "volume"],
                filters=["ticker IN ('AAPL', 'MSFT', 'NVDA')", "date >= '2024-01-01'"],
            )
        )
    )

    print("\n--- JSON Extract Expression (build_json_extract) ---")
    print(demo_json_extract_sql())

    print("\n--- Sector PIVOT SQL (build_pivot) ---")
    print(build_sector_pivot_sql("ohlcv"))

    print("\n--- Returns CTE SQL (CTEBuilder + QueryBuilder) ---")
    returns_sql = build_returns_cte_sql("ohlcv")
    print(returns_sql[:300] + "...\n")

    # Stdlib simulation
    print("--- Sector Averages (stdlib simulation) ---")
    sector_avgs = _simulate_sector_avg(records)
    rows = [[s, v["avg_close"], v["count"]] for s, v in sorted(sector_avgs.items())]
    _print_table(["sector", "avg_close", "n_records"], rows)

    print("\n--- Ticker Return Stats (stdlib simulation) ---")
    ticker_stats = _simulate_ticker_returns(records)
    _print_table(
        ["ticker", "sector", "days", "avg_ret%", "volatility", "worst", "best"],
        [
            [
                r["ticker"],
                r["sector"],
                r["trading_days"],
                r["avg_daily_return"],
                r["volatility"],
                r["worst_day"],
                r["best_day"],
            ]
            for r in ticker_stats
        ],
    )

    total_ms = (time.perf_counter() - t0) * 1000
    print(f"\nTotal ETL pipeline time: {total_ms:.1f} ms")


if __name__ == "__main__":
    main()
