import sqlite3
import time

import pytest

from pricequery.executor import QueryError, make_readonly_engine


def _db_file(demo_url):
    return demo_url[len("sqlite:///"):]


def test_select_returns_columns_and_rows(executor):
    result = executor.run("SELECT name FROM categories ORDER BY name", 100)
    assert result.columns == ["name"]
    assert [r[0] for r in result.rows] == ["Cooking", "Fiction", "Mystery", "Poetry", "Science", "Travel"]
    assert result.truncated is False


@pytest.mark.parametrize("sql", ["DELETE FROM products", "DROP TABLE products", "UPDATE categories SET name = 'x'",
                                 "INSERT INTO categories (name) VALUES ('x')"])
def test_database_connection_refuses_writes_even_when_the_guard_is_bypassed(executor, demo_url, sql):
    with pytest.raises(QueryError):
        executor.run(sql, 100)
    count = sqlite3.connect(_db_file(demo_url)).execute("SELECT COUNT(*) FROM products").fetchone()[0]
    assert count == 60


def test_rows_beyond_the_cap_are_cut_and_flagged(executor):
    result = executor.run("SELECT * FROM price_history", 7)
    assert len(result.rows) == 7 and result.truncated is True


def test_exactly_max_rows_is_not_flagged_as_truncated(executor):
    result = executor.run("SELECT * FROM categories", 6)
    assert len(result.rows) == 6 and result.truncated is False


def test_runaway_query_is_stopped_by_the_timeout_and_not_marked_retryable(demo_url):
    executor = make_readonly_engine(demo_url, timeout_seconds=0.3)
    started = time.monotonic()
    with pytest.raises(QueryError) as caught:
        executor.run("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n", 100)
    assert time.monotonic() - started < 5
    assert caught.value.retryable is False


def test_sql_errors_are_retryable_and_carry_the_database_message(executor):
    with pytest.raises(QueryError, match="no such column") as caught:
        executor.run("SELECT no_such_column FROM products", 100)
    assert caught.value.retryable is True


def test_colons_and_percent_signs_in_literals_are_not_treated_as_parameters(executor):
    result = executor.run("SELECT '12:30' AS t, title FROM products WHERE title LIKE '%Harbour%' AND 1 = 1", 100)
    assert result.rows and result.rows[0][0] == "12:30"
