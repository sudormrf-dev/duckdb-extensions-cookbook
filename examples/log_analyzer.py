"""Log analyzer example: parse synthetic JSON logs with DuckDB query patterns.

Demonstrates:
  - Generating 10,000 synthetic JSON log lines in memory
  - Using QueryBuilder for complex analytical queries (aggregate by hour, top endpoints)
  - Using CTEBuilder to chain multiple analysis steps
  - Running via DuckDB if available, otherwise simulating results with stdlib only

Usage::

    python examples/log_analyzer.py
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta, timezone

from patterns.query_builder import CTEBuilder, OrderDirection, QueryBuilder

# ---------------------------------------------------------------------------
# Synthetic log generation
# ---------------------------------------------------------------------------

ENDPOINTS = [
    "/api/v1/users",
    "/api/v1/orders",
    "/api/v1/products",
    "/api/v2/search",
    "/api/v2/recommendations",
    "/healthz",
    "/metrics",
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/payments",
]

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]

STATUS_WEIGHTS = [
    (200, 60),
    (201, 10),
    (204, 5),
    (400, 8),
    (401, 4),
    (403, 3),
    (404, 5),
    (429, 2),
    (500, 2),
    (503, 1),
]

SERVICES = ["gateway", "auth", "catalog", "fulfillment", "payment"]


def _weighted_choice(choices: list[tuple[int, int]], rng: random.Random) -> int:
    """Return a weighted random choice from (value, weight) pairs."""
    population = [v for v, w in choices for _ in range(w)]
    return rng.choice(population)


def generate_log_lines(n: int = 10_000, seed: int = 42) -> list[str]:
    """Generate *n* synthetic JSON log lines.

    Each line is a compact JSON object representing one HTTP request event.

    Args:
        n: Number of log lines to generate.
        seed: Random seed for reproducibility.

    Returns:
        List of JSON strings (one log entry per item).
    """
    rng = random.Random(seed)
    base_ts = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    lines: list[str] = []

    for i in range(n):
        ts = base_ts + timedelta(seconds=i * 8 + rng.randint(0, 7))
        status = _weighted_choice(STATUS_WEIGHTS, rng)
        entry = {
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": rng.choice(HTTP_METHODS),
            "endpoint": rng.choice(ENDPOINTS),
            "status": status,
            "latency_ms": rng.randint(5, 2000) if status >= 500 else rng.randint(5, 400),
            "service": rng.choice(SERVICES),
            "request_id": f"req-{i:07d}",
            "user_id": rng.randint(1000, 9999) if status not in {401, 403} else None,
            "error": status >= 400,
        }
        lines.append(json.dumps(entry, separators=(",", ":")))

    return lines


# ---------------------------------------------------------------------------
# Query builders — demonstrating patterns/query_builder.py
# ---------------------------------------------------------------------------


def build_hourly_aggregation_sql(table: str) -> str:
    """Build a query that aggregates requests per hour using QueryBuilder.

    Args:
        table: Source table name containing parsed log data.

    Returns:
        SQL string for hourly request counts and p95 latency.
    """
    return (
        QueryBuilder(table)
        .select(
            "strftime(ts, '%Y-%m-%d %H:00') AS hour",
            "COUNT(*) AS total_requests",
            "SUM(CASE WHEN error THEN 1 ELSE 0 END) AS error_count",
            "ROUND(100.0 * SUM(CASE WHEN error THEN 1 ELSE 0 END) / COUNT(*), 2) AS error_rate_pct",
            "ROUND(AVG(latency_ms), 1) AS avg_latency_ms",
            "QUANTILE_CONT(latency_ms, 0.95) AS p95_latency_ms",
        )
        .group_by("hour")
        .order_by("hour", OrderDirection.ASC)
        .build()
    )


def build_top_endpoints_sql(table: str, limit: int = 5) -> str:
    """Build a query for the top endpoints by request count.

    Args:
        table: Source table name.
        limit: Number of top endpoints to return.

    Returns:
        SQL string.
    """
    return (
        QueryBuilder(table)
        .select(
            "endpoint",
            "method",
            "COUNT(*) AS requests",
            "SUM(CASE WHEN error THEN 1 ELSE 0 END) AS errors",
            "ROUND(AVG(latency_ms), 1) AS avg_latency_ms",
        )
        .group_by("endpoint", "method")
        .order_by("requests", OrderDirection.DESC)
        .limit(limit)
        .build()
    )


def build_error_analysis_sql(table: str) -> str:
    """Build a CTE-based query that finds error spikes using CTEBuilder.

    Args:
        table: Source table name.

    Returns:
        Full WITH ... SELECT SQL string.
    """
    hourly_errors = (
        QueryBuilder(table)
        .select(
            "strftime(ts, '%Y-%m-%d %H:00') AS hour",
            "status",
            "COUNT(*) AS occurrences",
        )
        .where("error = true")
        .group_by("hour", "status")
        .build()
    )

    ranked = "SELECT *, ROW_NUMBER() OVER (PARTITION BY hour ORDER BY occurrences DESC) AS rk FROM hourly_errors"

    final = (
        QueryBuilder("ranked")
        .select("hour", "status", "occurrences")
        .where("rk = 1")
        .order_by("occurrences", OrderDirection.DESC)
        .limit(10)
        .build()
    )

    return (
        CTEBuilder()
        .with_cte("hourly_errors", hourly_errors)
        .with_cte("ranked", ranked)
        .build(final)
    )


# ---------------------------------------------------------------------------
# DuckDB execution (optional) + pure-Python simulation
# ---------------------------------------------------------------------------


def _simulate_hourly(logs: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    """Aggregate logs by hour using stdlib (no DuckDB required)."""
    from collections import defaultdict

    buckets: dict[str, dict[str, list[int] | int]] = defaultdict(
        lambda: {"total": 0, "errors": 0, "latencies": []}
    )
    for entry in logs:
        hour = entry["ts"][:13] + ":00"
        bucket = buckets[hour]
        bucket["total"] = int(bucket["total"]) + 1  # type: ignore[arg-type]
        if entry["error"]:
            bucket["errors"] = int(bucket["errors"]) + 1  # type: ignore[arg-type]
        cast_latencies: list[int] = bucket["latencies"]  # type: ignore[assignment]
        cast_latencies.append(entry["latency_ms"])

    results = []
    for hour in sorted(buckets):
        b = buckets[hour]
        total = int(b["total"])  # type: ignore[arg-type]
        errors = int(b["errors"])  # type: ignore[arg-type]
        lats: list[int] = b["latencies"]  # type: ignore[assignment]
        lats_sorted = sorted(lats)
        p95_idx = int(len(lats_sorted) * 0.95)
        results.append(
            {
                "hour": hour,
                "total_requests": total,
                "error_count": errors,
                "error_rate_pct": round(100.0 * errors / total, 2) if total else 0.0,
                "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else 0.0,
                "p95_latency_ms": lats_sorted[p95_idx] if lats_sorted else 0,
            }
        )
    return results


def _simulate_top_endpoints(logs: list[dict], limit: int = 5) -> list[dict]:  # type: ignore[type-arg]
    """Return top endpoints by count using stdlib."""
    from collections import defaultdict

    counts: dict[tuple[str, str], dict[str, int | float]] = defaultdict(
        lambda: {"requests": 0, "errors": 0, "total_lat": 0}
    )
    for entry in logs:
        key = (entry["endpoint"], entry["method"])
        counts[key]["requests"] = int(counts[key]["requests"]) + 1
        if entry["error"]:
            counts[key]["errors"] = int(counts[key]["errors"]) + 1
        counts[key]["total_lat"] = int(counts[key]["total_lat"]) + entry["latency_ms"]

    results = []
    for (ep, method), v in counts.items():
        req = int(v["requests"])
        results.append(
            {
                "endpoint": ep,
                "method": method,
                "requests": req,
                "errors": int(v["errors"]),
                "avg_latency_ms": round(float(v["total_lat"]) / req, 1) if req else 0.0,
            }
        )
    results.sort(key=lambda r: r["requests"], reverse=True)  # type: ignore[return-value]
    return results[:limit]


def run_analysis(logs: list[dict]) -> None:  # type: ignore[type-arg]
    """Run the full log analysis, preferring DuckDB when available.

    Args:
        logs: Parsed log dictionaries.
    """
    print("\n=== Log Analyzer ===")
    print(f"Loaded {len(logs):,} log entries\n")

    try:
        import duckdb  # type: ignore[import]

        con = duckdb.connect()
        con.execute(
            "CREATE TABLE logs AS SELECT * FROM (VALUES %s) t"
            % ", ".join(
                f"('{e['ts']}', '{e['method']}', '{e['endpoint']}', {e['status']}, "
                f"{e['latency_ms']}, '{e['service']}', '{e['request_id']}', "
                f"{'NULL' if e['user_id'] is None else e['user_id']}, {str(e['error']).lower()})"
                for e in logs[:10]  # small sample for schema
            )
        )
        # Use DuckDB's JSON table function for the full dataset
        json_data = json.dumps(logs)
        con.execute("DROP TABLE logs")
        con.execute(f"CREATE TABLE logs AS SELECT * FROM read_json_auto('{json_data}')")

        hourly_sql = build_hourly_aggregation_sql("logs")
        print("--- Hourly Aggregation SQL (via QueryBuilder) ---")
        print(hourly_sql[:200] + "...\n")
        rows = con.execute(hourly_sql).fetchall()
        _print_table(["hour", "total", "errors", "err%", "avg_ms", "p95_ms"], rows, limit=5)

        top_sql = build_top_endpoints_sql("logs")
        print("\n--- Top 5 Endpoints ---")
        rows = con.execute(top_sql).fetchall()
        _print_table(["endpoint", "method", "requests", "errors", "avg_ms"], rows)
        con.close()

    except Exception:
        print("DuckDB not available — running stdlib simulation\n")

        hourly_sql = build_hourly_aggregation_sql("logs")
        print("--- Generated SQL (QueryBuilder demo) ---")
        print(hourly_sql)

        error_sql = build_error_analysis_sql("logs")
        print("\n--- Generated CTE SQL (CTEBuilder demo) ---")
        print(error_sql[:300] + "...\n")

        hourly = _simulate_hourly(logs)
        print("--- Hourly Aggregation (stdlib simulation) ---")
        _print_table(
            [
                "hour",
                "total_requests",
                "error_count",
                "error_rate_pct",
                "avg_latency_ms",
                "p95_latency_ms",
            ],
            [
                [
                    r["hour"],
                    r["total_requests"],
                    r["error_count"],
                    r["error_rate_pct"],
                    r["avg_latency_ms"],
                    r["p95_latency_ms"],
                ]
                for r in hourly
            ],
            limit=5,
        )

        top = _simulate_top_endpoints(logs)
        print("\n--- Top 5 Endpoints (stdlib simulation) ---")
        _print_table(
            ["endpoint", "method", "requests", "errors", "avg_latency_ms"],
            [
                [r["endpoint"], r["method"], r["requests"], r["errors"], r["avg_latency_ms"]]
                for r in top
            ],
        )


def _print_table(headers: list[str], rows: list, limit: int | None = None) -> None:
    """Print a simple aligned table to stdout.

    Args:
        headers: Column header names.
        rows: Iterable of row tuples/lists.
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
    if limit and len(rows) > limit:
        print(f"  ... ({len(rows) - limit} more rows)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Generate logs, build SQL queries, and run the analysis."""
    t0 = time.perf_counter()
    print("Generating 10,000 synthetic log lines...")
    raw_lines = generate_log_lines(10_000)
    logs = [json.loads(line) for line in raw_lines]
    gen_ms = (time.perf_counter() - t0) * 1000
    print(f"Generated in {gen_ms:.1f} ms")

    run_analysis(logs)


if __name__ == "__main__":
    main()
