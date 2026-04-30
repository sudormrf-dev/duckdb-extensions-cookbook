from __future__ import annotations
"""Query performance benchmarks: compare DuckDB query strategies on 100,000 rows.

Benchmarks:
  1. Filter-first vs join-first
  2. Aggregation with vs without projection pushdown
  3. JSON extraction inline vs pre-parsed column

Each benchmark generates in-memory data, measures wall time with time.perf_counter,
and prints a results table with speedup ratios.

Usage::

    python benchmarks/query_performance.py
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from patterns.query_builder import OrderDirection, QueryBuilder

# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------

CATEGORIES = ["electronics", "clothing", "food", "books", "sports", "home", "toys", "auto"]
REGIONS = ["north", "south", "east", "west", "central"]
STATUSES = ["completed", "pending", "cancelled", "refunded"]


@dataclass(frozen=True)
class BenchResult:
    """Result from a single benchmark run.

    Attributes:
        name: Benchmark variant name.
        elapsed_ms: Wall time in milliseconds.
        rows_processed: Number of input rows.
    """

    name: str
    elapsed_ms: float
    rows_processed: int


def generate_orders(n: int = 100_000, seed: int = 7) -> list[dict]:  # type: ignore[type-arg]
    """Generate *n* synthetic order rows in memory.

    Each row simulates a flattened e-commerce order record with a JSON metadata blob.

    Args:
        n: Number of rows to generate.
        seed: Random seed for reproducibility.

    Returns:
        List of order dicts.
    """
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        category = rng.choice(CATEGORIES)
        rows.append(
            {
                "order_id": i + 1,
                "customer_id": rng.randint(1, 10_000),
                "category": category,
                "region": rng.choice(REGIONS),
                "status": rng.choice(STATUSES),
                "amount": round(rng.uniform(5.0, 2000.0), 2),
                "quantity": rng.randint(1, 20),
                "discount_pct": rng.choice([0, 5, 10, 15, 20]),
                "year": rng.randint(2021, 2024),
                "month": rng.randint(1, 12),
                "metadata": json.dumps(
                    {"source": rng.choice(["web", "mobile", "api"]), "category": category},
                    separators=(",", ":"),
                ),
            }
        )
    return rows


def generate_customers(n_orders: int, seed: int = 7) -> list[dict]:  # type: ignore[type-arg]
    """Generate a customers lookup table matching the orders dataset.

    Args:
        n_orders: Used to derive the customer_id range (1 to 10,000).
        seed: Random seed.

    Returns:
        List of customer dicts.
    """
    _ = n_orders
    rng = random.Random(seed + 1)
    tiers = ["bronze", "silver", "gold", "platinum"]
    return [
        {
            "customer_id": cid,
            "tier": rng.choice(tiers),
            "country": rng.choice(["US", "CA", "GB", "DE", "FR"]),
        }
        for cid in range(1, 10_001)
    ]


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------


def _time_fn(fn: object, repeat: int = 3) -> float:
    """Return median elapsed ms over *repeat* calls.

    Args:
        fn: Zero-argument callable to benchmark.
        repeat: Number of repetitions.

    Returns:
        Median wall time in milliseconds.
    """
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return times[len(times) // 2]


# ---------------------------------------------------------------------------
# Benchmark 1: Filter-first vs join-first (stdlib simulation)
# ---------------------------------------------------------------------------


def bench_filter_first(orders: list[dict], customers: dict[int, dict]) -> BenchResult:  # type: ignore[type-arg]
    """Filter orders first, then join (better strategy for selective filters).

    Args:
        orders: Full orders list.
        customers: Customer lookup by customer_id.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        filtered = [o for o in orders if o["status"] == "completed" and o["amount"] > 500]
        _result = [
            {**o, "tier": customers.get(o["customer_id"], {}).get("tier", "unknown")}
            for o in filtered
        ]

    elapsed = _time_fn(_run)
    return BenchResult("filter-first", elapsed, len(orders))


def bench_join_first(orders: list[dict], customers: dict[int, dict]) -> BenchResult:  # type: ignore[type-arg]
    """Join first, then filter (less efficient when filter is selective).

    Args:
        orders: Full orders list.
        customers: Customer lookup by customer_id.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        joined = [
            {**o, "tier": customers.get(o["customer_id"], {}).get("tier", "unknown")}
            for o in orders
        ]
        _result = [r for r in joined if r["status"] == "completed" and r["amount"] > 500]

    elapsed = _time_fn(_run)
    return BenchResult("join-first", elapsed, len(orders))


# ---------------------------------------------------------------------------
# Benchmark 2: Aggregation with vs without projection pushdown
# ---------------------------------------------------------------------------


def bench_agg_with_pushdown(orders: list[dict]) -> BenchResult:  # type: ignore[type-arg]
    """Aggregate only the columns needed (projection pushdown simulation).

    Args:
        orders: Full orders list.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for o in orders:
            cat = o["category"]
            totals[cat] = totals.get(cat, 0.0) + o["amount"]
            counts[cat] = counts.get(cat, 0) + 1
        _result = {cat: totals[cat] / counts[cat] for cat in totals}

    elapsed = _time_fn(_run)
    return BenchResult("agg-with-pushdown", elapsed, len(orders))


def bench_agg_without_pushdown(orders: list[dict]) -> BenchResult:  # type: ignore[type-arg]
    """Aggregate after copying all columns (no pushdown).

    Args:
        orders: Full orders list.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        # Simulate reading all columns into a new list before aggregating
        full_copy = [dict(o) for o in orders]
        totals: dict[str, float] = {}
        counts: dict[str, int] = {}
        for o in full_copy:
            cat = o["category"]
            totals[cat] = totals.get(cat, 0.0) + o["amount"]
            counts[cat] = counts.get(cat, 0) + 1
        _result = {cat: totals[cat] / counts[cat] for cat in totals}

    elapsed = _time_fn(_run)
    return BenchResult("agg-without-pushdown", elapsed, len(orders))


# ---------------------------------------------------------------------------
# Benchmark 3: JSON extraction inline vs pre-parsed
# ---------------------------------------------------------------------------


def bench_json_inline(orders: list[dict]) -> BenchResult:  # type: ignore[type-arg]
    """Parse JSON on every access (inline extraction).

    Args:
        orders: Orders with JSON metadata column.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        _result = [json.loads(o["metadata"])["source"] for o in orders]

    elapsed = _time_fn(_run)
    return BenchResult("json-inline", elapsed, len(orders))


def bench_json_preparsed(orders: list[dict]) -> BenchResult:  # type: ignore[type-arg]
    """Pre-parse JSON once, then access fields (pre-parsed column).

    Args:
        orders: Orders with JSON metadata column.

    Returns:
        Benchmark result.
    """

    def _run() -> None:
        parsed = [json.loads(o["metadata"]) for o in orders]
        _result = [p["source"] for p in parsed]

    elapsed = _time_fn(_run)
    return BenchResult("json-preparsed", elapsed, len(orders))


# ---------------------------------------------------------------------------
# SQL query printing (shows the QueryBuilder patterns)
# ---------------------------------------------------------------------------


def print_equivalent_sql() -> None:
    """Print the equivalent DuckDB SQL for each benchmark strategy."""
    print("\n--- Equivalent DuckDB SQL (QueryBuilder patterns) ---\n")

    filter_first_sql = (
        QueryBuilder(
            "(SELECT * FROM orders WHERE status = 'completed' AND amount > 500) AS filtered"
        )
        .select("filtered.*", "c.tier")
        .join("customers c", "filtered.customer_id = c.customer_id")
        .build()
    )
    print("1a. Filter-first SQL:")
    print(filter_first_sql)

    join_first_sql = (
        QueryBuilder("orders o")
        .select("o.*", "c.tier")
        .join("customers c", "o.customer_id = c.customer_id")
        .where("o.status = 'completed'", "o.amount > 500")
        .build()
    )
    print("\n1b. Join-first SQL:")
    print(join_first_sql)

    pushdown_sql = (
        QueryBuilder("orders")
        .select("category", "ROUND(AVG(amount), 2) AS avg_amount")
        .group_by("category")
        .order_by("avg_amount", OrderDirection.DESC)
        .build()
    )
    print("\n2a. Agg with pushdown SQL:")
    print(pushdown_sql)

    json_inline_sql = (
        QueryBuilder("orders").select("order_id", "metadata->>'source' AS source").build()
    )
    print("\n3a. JSON inline extraction SQL (DuckDB arrow operator):")
    print(json_inline_sql)


# ---------------------------------------------------------------------------
# Results printer
# ---------------------------------------------------------------------------


def print_results(groups: list[tuple[BenchResult, BenchResult]], title: str) -> None:
    """Print a benchmark results table with speedup ratios.

    Args:
        groups: Pairs of (baseline, alternative) benchmark results.
        title: Section title.
    """
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    headers = ["Strategy", "Rows", "Median ms", "vs baseline"]
    col_w = [max(len(h), 20) for h in headers]

    fmt = "  ".join(f"{{:<{w}}}" for w in col_w)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_w))

    for baseline, alternative in groups:
        speedup = (
            baseline.elapsed_ms / alternative.elapsed_ms if alternative.elapsed_ms > 0 else 1.0
        )
        direction = "faster" if speedup > 1 else "slower"
        print(
            fmt.format(
                baseline.name,
                f"{baseline.rows_processed:,}",
                f"{baseline.elapsed_ms:.2f}",
                "baseline",
            )
        )
        print(
            fmt.format(
                alternative.name,
                f"{alternative.rows_processed:,}",
                f"{alternative.elapsed_ms:.2f}",
                f"{speedup:.2f}x {direction}",
            )
        )
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Generate data, run all benchmarks, and print results."""
    print("=== DuckDB Query Performance Benchmarks ===")
    print("Generating 100,000 synthetic order rows...")

    t0 = time.perf_counter()
    orders = generate_orders(100_000)
    raw_customers = generate_customers(len(orders))
    customers_by_id = {c["customer_id"]: c for c in raw_customers}
    gen_ms = (time.perf_counter() - t0) * 1000
    print(f"Data generation: {gen_ms:.1f} ms\n")

    # Benchmark 1: Filter-first vs join-first
    print("Running benchmark 1: filter-first vs join-first...")
    r_filter = bench_filter_first(orders, customers_by_id)
    r_join = bench_join_first(orders, customers_by_id)

    # Benchmark 2: Aggregation pushdown
    print("Running benchmark 2: aggregation with vs without pushdown...")
    r_agg_push = bench_agg_with_pushdown(orders)
    r_agg_nopush = bench_agg_without_pushdown(orders)

    # Benchmark 3: JSON extraction
    print("Running benchmark 3: JSON inline vs pre-parsed...")
    r_json_inline = bench_json_inline(orders)
    r_json_pre = bench_json_preparsed(orders)

    # Print results
    print_results(
        [(r_join, r_filter)],
        "Benchmark 1: Filter-first vs Join-first",
    )
    print_results(
        [(r_agg_nopush, r_agg_push)],
        "Benchmark 2: Aggregation — Pushdown vs No Pushdown",
    )
    print_results(
        [(r_json_inline, r_json_pre)],
        "Benchmark 3: JSON Extraction — Inline vs Pre-parsed",
    )

    print_equivalent_sql()

    total_ms = (time.perf_counter() - t0) * 1000
    print(f"\nTotal benchmark time: {total_ms:.1f} ms")


if __name__ == "__main__":
    main()
