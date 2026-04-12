"""Tests for query_builder.py."""

from __future__ import annotations

from patterns.query_builder import (
    CTEBuilder,
    JoinType,
    OrderDirection,
    QueryBuilder,
    WindowClause,
)


class TestWindowClause:
    def test_empty(self):
        w = WindowClause()
        assert w.to_sql() == "OVER ()"

    def test_partition_by(self):
        w = WindowClause(partition_by=["user_id"])
        assert "PARTITION BY user_id" in w.to_sql()

    def test_order_by(self):
        w = WindowClause(order_by=["ts"])
        assert "ORDER BY ts" in w.to_sql()

    def test_order_direction(self):
        w = WindowClause(order_by=["ts"], direction=OrderDirection.DESC)
        assert "DESC" in w.to_sql()

    def test_frame(self):
        w = WindowClause(frame="ROWS BETWEEN 3 PRECEDING AND CURRENT ROW")
        assert "ROWS BETWEEN" in w.to_sql()

    def test_with_frame(self):
        w = WindowClause(partition_by=["u"])
        w2 = w.with_frame("ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW")
        assert "UNBOUNDED PRECEDING" in w2.to_sql()
        # original unchanged
        assert "ROWS" not in w.to_sql()

    def test_full_spec(self):
        w = WindowClause(
            partition_by=["dept"],
            order_by=["salary"],
            direction=OrderDirection.DESC,
        )
        sql = w.to_sql()
        assert "PARTITION BY dept" in sql
        assert "ORDER BY salary DESC" in sql


class TestQueryBuilder:
    def test_select_star_default(self):
        q = QueryBuilder("orders")
        assert "SELECT *" in q.build()

    def test_select_columns(self):
        q = QueryBuilder("orders").select("id", "amount")
        assert "id, amount" in q.build()

    def test_from_table(self):
        q = QueryBuilder("orders")
        assert "FROM orders" in q.build()

    def test_where(self):
        q = QueryBuilder("t").where("amount > 0")
        assert "WHERE amount > 0" in q.build()

    def test_multiple_where_anded(self):
        q = QueryBuilder("t").where("a > 0", "b < 10")
        sql = q.build()
        assert "AND" in sql

    def test_group_by(self):
        q = QueryBuilder("t").select("dept", "SUM(salary)").group_by("dept")
        assert "GROUP BY dept" in q.build()

    def test_having(self):
        q = QueryBuilder("t").group_by("dept").having("COUNT(*) > 5")
        assert "HAVING COUNT(*) > 5" in q.build()

    def test_order_by_asc(self):
        q = QueryBuilder("t").order_by("name")
        assert "ORDER BY name ASC" in q.build()

    def test_order_by_desc(self):
        q = QueryBuilder("t").order_by("score", OrderDirection.DESC)
        assert "ORDER BY score DESC" in q.build()

    def test_limit(self):
        q = QueryBuilder("t").limit(10)
        assert "LIMIT 10" in q.build()

    def test_offset(self):
        q = QueryBuilder("t").limit(10).offset(20)
        assert "OFFSET 20" in q.build()

    def test_distinct(self):
        q = QueryBuilder("t").select("dept").distinct()
        assert "SELECT DISTINCT" in q.build()

    def test_join_inner(self):
        q = QueryBuilder("orders o").join("customers c", "o.customer_id = c.id")
        assert "INNER JOIN customers c" in q.build()

    def test_join_left(self):
        q = QueryBuilder("t").join("u", "t.id = u.id", JoinType.LEFT)
        assert "LEFT JOIN" in q.build()

    def test_qualify(self):
        q = QueryBuilder("t").qualify("ROW_NUMBER() OVER () = 1")
        assert "QUALIFY" in q.build()

    def test_no_table(self):
        q = QueryBuilder().select("1 + 1 AS result")
        sql = q.build()
        assert "FROM" not in sql

    def test_count_wrapper(self):
        q = QueryBuilder("orders").where("amount > 0")
        count_sql = q.count()
        assert "COUNT(*)" in count_sql
        assert "orders" in count_sql

    def test_chaining_returns_self(self):
        q = QueryBuilder("t")
        assert q.select("x") is q
        assert q.where("x > 0") is q
        assert q.limit(5) is q


class TestCTEBuilder:
    def test_empty_cte(self):
        cte = CTEBuilder()
        assert cte.build("SELECT 1") == "SELECT 1"

    def test_single_cte(self):
        sql = CTEBuilder().with_cte("ranked", "SELECT * FROM t").build("SELECT * FROM ranked")
        assert "WITH" in sql
        assert "ranked AS" in sql

    def test_multiple_ctes(self):
        sql = (
            CTEBuilder()
            .with_cte("a", "SELECT 1")
            .with_cte("b", "SELECT 2")
            .build("SELECT * FROM b")
        )
        assert "a AS" in sql
        assert "b AS" in sql

    def test_recursive_flag(self):
        sql = CTEBuilder().recursive().with_cte("r", "SELECT 1").build("SELECT * FROM r")
        assert "WITH RECURSIVE" in sql

    def test_cte_names(self):
        cte = CTEBuilder().with_cte("a", "SELECT 1").with_cte("b", "SELECT 2")
        assert cte.cte_names() == ["a", "b"]

    def test_len(self):
        cte = CTEBuilder().with_cte("a", "SELECT 1")
        assert len(cte) == 1

    def test_final_query_present(self):
        final = "SELECT * FROM ranked"
        sql = CTEBuilder().with_cte("ranked", "SELECT 1").build(final)
        assert final in sql
