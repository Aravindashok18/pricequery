import sqlite3

import pytest

from pricequery.executor import QueryError
from pricequery.guard import UnsafeSQL
from pricequery.nl2sql import CannotAnswer, extract_sql


def test_extract_sql_handles_fences_and_bare_sql():
    assert extract_sql("```sql\nSELECT 1\n```") == "SELECT 1"
    assert extract_sql("```\nSELECT 2\n```") == "SELECT 2"
    assert extract_sql("  SELECT 3  ") == "SELECT 3"


def test_happy_path_runs_the_generated_query_with_a_row_limit(make_engine):
    engine, llm = make_engine("```sql\nSELECT COUNT(*) FROM products\n```")
    answer = engine.answer("How many products are there?")
    assert answer.result.rows == [(60,)]
    assert answer.attempts == 1 and llm.calls == 1
    assert answer.sql == "SELECT COUNT(*) FROM products LIMIT 101"


def test_prompt_contains_schema_examples_date_and_dialect_but_not_blocked_columns(make_engine):
    engine, llm = make_engine("SELECT 1 FROM products")
    engine.answer("anything")
    prompt = llm.prompts[0]
    for expected in ["TABLE price_history", "scraped_at", "REFERENCES categories.id", "2026-10-01", "sqlite",
                     "Question: How many products are in each category?", "NO_QUERY", "the CURRENT price"]:
        assert expected in prompt, expected
    assert "email" not in prompt.lower()
    assert "anything" in prompt


def test_sql_error_triggers_exactly_one_retry_with_the_error_in_the_prompt(make_engine):
    engine, llm = make_engine(
        "SELECT title, cost FROM products",  # no such column
        "SELECT title FROM products",
    )
    answer = engine.answer("List the titles")
    assert answer.attempts == 2 and llm.calls == 2
    assert len(answer.result.rows) == 60
    second_prompt = llm.prompts[1]
    assert "SELECT title, cost FROM products" in second_prompt
    assert "no such column" in second_prompt
    assert answer.history[0]["error"] and answer.history[1]["error"] is None


def test_a_second_failure_is_raised_not_retried_again(make_engine):
    engine, llm = make_engine("SELECT a FROM products", "SELECT b FROM products", "SELECT title FROM products")
    with pytest.raises(QueryError, match="no such column: b"):
        engine.answer("List the titles")
    assert llm.calls == 2  # one retry, no more


def test_timeouts_are_not_retried(make_engine, executor, monkeypatch):
    def slow(sql, max_rows):
        raise QueryError("interrupted", retryable=False)

    monkeypatch.setattr(executor, "run", slow)
    engine, llm = make_engine("SELECT title FROM products", "SELECT title FROM products")
    with pytest.raises(QueryError):
        engine.answer("List the titles")
    assert llm.calls == 1


@pytest.mark.parametrize("reply", ["DROP TABLE products", "DELETE FROM products WHERE 1 = 1",
                                   "SELECT 1; DROP TABLE products", "UPDATE products SET title = 'x'"])
def test_unsafe_sql_is_refused_without_a_retry_and_nothing_is_changed(make_engine, demo_url, reply):
    engine, llm = make_engine(reply, "SELECT 1")
    with pytest.raises(UnsafeSQL):
        engine.answer("do something bad")
    assert llm.calls == 1
    assert sqlite3.connect(demo_url[len("sqlite:///"):]).execute("SELECT COUNT(*) FROM products").fetchone()[0] == 60


def test_blocked_column_is_refused(make_engine):
    engine, _ = make_engine("SELECT email FROM price_alerts")
    with pytest.raises(UnsafeSQL, match="email"):
        engine.answer("Who subscribes to alerts?")


def test_no_query_reply_means_the_schema_cannot_answer(make_engine):
    engine, llm = make_engine("NO_QUERY")
    with pytest.raises(CannotAnswer):
        engine.answer("How many reviews does each product have?")
    assert llm.calls == 1


def test_results_are_capped_at_the_row_limit(make_engine):
    engine, _ = make_engine("SELECT * FROM price_history")
    answer = engine.answer("everything")
    assert len(answer.result.rows) == 100 and answer.result.truncated is True
    assert answer.sql.endswith("LIMIT 101")


def test_a_larger_limit_from_the_model_is_lowered_and_the_cut_is_reported(make_engine):
    engine, _ = make_engine("SELECT * FROM price_history LIMIT 100000")
    answer = engine.answer("everything")
    assert len(answer.result.rows) == 100 and answer.result.truncated is True
    assert answer.sql.endswith("LIMIT 101")


def test_a_result_that_fits_is_not_reported_as_truncated(make_engine):
    engine, _ = make_engine("SELECT name FROM categories")
    assert engine.answer("categories").result.truncated is False
