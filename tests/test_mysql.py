"""The same pipeline against a real MySQL server.

Skipped unless PRICEQUERY_MYSQL_ADMIN_URL points at a server where the account may create
databases and users, for example a scratch instance:

    PRICEQUERY_MYSQL_ADMIN_URL=mysql+pymysql://root:@127.0.0.1:3306/ pytest tests/test_mysql.py

The tests create a database called pricequery_test and a user called pricequery_ro, and drop
both afterwards. Do not point this at a server whose databases or users matter.
"""
import os
import re
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pricequery import guard
from pricequery.api import create_app
from pricequery.executor import QueryError, make_readonly_engine
from pricequery.nl2sql import PriceQuery
from pricequery.schema import describe_schema
from pricequery.seed import build_demo_data

from tests.conftest import TODAY, ScriptedLLM

ADMIN_URL = os.environ.get("PRICEQUERY_MYSQL_ADMIN_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="set PRICEQUERY_MYSQL_ADMIN_URL to run the MySQL tests")

ROOT = Path(__file__).resolve().parent.parent
DATABASE = "pricequery_test"
RO_USER = "pricequery_ro"
RO_PASSWORD = "test-only-password"


def run_script(engine, script: str) -> None:
    """Run a .sql file: statements end with ';' at the end of a line, '--' lines are comments."""
    body = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    with engine.begin() as conn:
        for statement in re.split(r";\s*(?:\n|$)", body):
            if statement.strip():
                conn.exec_driver_sql(statement)


@pytest.fixture(scope="module")
def mysql():
    admin = create_engine(ADMIN_URL)
    with admin.begin() as conn:
        conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {DATABASE}")
        conn.exec_driver_sql(f"DROP USER IF EXISTS '{RO_USER}'@'localhost'")
        conn.exec_driver_sql(f"CREATE DATABASE {DATABASE}")

    admin_url = make_url(ADMIN_URL).set(database=DATABASE)
    db_admin = create_engine(admin_url)
    run_script(db_admin, (ROOT / "db" / "schema.sql").read_text())  # the shipped DDL, not the ORM's create_all
    with Session(db_admin) as session:
        build_demo_data(session)
        session.commit()
    grants = (ROOT / "db" / "readonly_user.sql").read_text().replace("<choose-a-password>", RO_PASSWORD).replace("pricedb.", f"{DATABASE}.")
    run_script(admin, grants)

    urls = {
        "admin": admin_url.render_as_string(hide_password=False),
        "ro": make_url(ADMIN_URL).set(username=RO_USER, password=RO_PASSWORD, database=DATABASE).render_as_string(hide_password=False),
    }
    yield urls

    with admin.begin() as conn:
        conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {DATABASE}")
        conn.exec_driver_sql(f"DROP USER IF EXISTS '{RO_USER}'@'localhost'")


@pytest.fixture()
def ro(mysql):
    executor = make_readonly_engine(mysql["ro"], timeout_seconds=2)
    yield executor
    executor.engine.dispose()


def count(url: str, table: str) -> int:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
    finally:
        engine.dispose()


# --- the shipped SQL files ---------------------------------------------------------------------

def test_schema_sql_and_the_orm_agree_on_the_seeded_data(mysql):
    assert count(mysql["admin"], "categories") == 6
    assert count(mysql["admin"], "products") == 60
    assert count(mysql["admin"], "price_history") == 60 * 90
    assert count(mysql["admin"], "price_alerts") == 15


def test_the_model_is_shown_a_schema_without_the_email_column(ro):
    schema = describe_schema(ro.engine, ("email",))

    assert "TABLE price_history" in schema and "REFERENCES products.id" in schema
    assert "email" not in schema and "target_price" in schema


# --- reading ----------------------------------------------------------------------------------------

def test_selects_run_and_the_row_cap_is_enforced(ro):
    result = ro.run("SELECT id, title FROM products ORDER BY id", 10)

    assert result.columns == ["id", "title"] and len(result.rows) == 10 and result.truncated is True


def test_percent_signs_and_colons_inside_literals_survive(ro):
    assert ro.run("SELECT COUNT(*) FROM products WHERE title LIKE '%Mirror%'", 10).rows[0][0] > 0
    assert ro.run("SELECT COUNT(*) FROM products WHERE title = 'a:b'", 10).rows == [(0,)]


@pytest.mark.parametrize("clause", ["LIMIT 5", "LIMIT 5 OFFSET 2", "LIMIT 2, 5", "LIMIT 1000"])
def test_the_guards_row_limit_rewrite_is_valid_mysql(ro, clause):
    safe = guard.prepare(f"SELECT id FROM products ORDER BY id {clause}", 10, ("email",))

    assert 0 < len(ro.run(safe, 10).rows) <= 10


# --- writing is refused, by two independent layers --------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DELETE FROM products", "UPDATE products SET title = 'x'", "INSERT INTO categories (name) VALUES ('x')",
    "DROP TABLE products", "TRUNCATE TABLE price_history", "CREATE TABLE evil (id INT)",
])
def test_the_database_user_cannot_write(mysql, ro, sql):
    # DML is refused by the grant ("command denied"); DDL by the read-only session first ("READ ONLY transaction")
    with pytest.raises(QueryError, match="(?i)denied|read only"):
        ro.run(sql, 10)

    assert count(mysql["admin"], "products") == 60 and count(mysql["admin"], "price_history") == 5400


def test_even_a_user_who_may_write_is_stopped_by_the_read_only_session(mysql):
    # the second layer on its own: connect as the account that owns the data
    executor = make_readonly_engine(mysql["admin"], timeout_seconds=2)

    try:
        with pytest.raises(QueryError, match="(?i)read only"):
            executor.run("DELETE FROM price_alerts", 10)
    finally:
        executor.engine.dispose()

    assert count(mysql["admin"], "price_alerts") == 15


def test_the_email_column_is_unreadable_but_the_rest_of_the_table_is_not(ro):
    with pytest.raises(QueryError, match="(?i)denied"):
        ro.run("SELECT email FROM price_alerts", 10)
    with pytest.raises(QueryError, match="(?i)denied"):
        ro.run("SELECT * FROM price_alerts", 10)

    assert len(ro.run("SELECT id, product_id, target_price, triggered_at FROM price_alerts", 100).rows) == 15


def test_a_runaway_query_is_stopped_by_the_server_side_timeout(ro):
    started = time.monotonic()

    # (a bare COUNT(*) over a cross join is computed arithmetically, so the predicate forces a real scan)
    with pytest.raises(QueryError) as error:
        ro.run("SELECT COUNT(*) FROM price_history a, price_history b, price_history c WHERE a.price + b.price + c.price > 1000000", 10)

    assert error.value.retryable is False  # a timeout, so the model is not asked to rewrite it
    assert time.monotonic() - started < 6


# --- the whole pipeline ---------------------------------------------------------------------------------

@pytest.fixture()
def make_engine(mysql):
    executors = []

    def build(*replies):
        llm = ScriptedLLM(*replies)
        executor = make_readonly_engine(mysql["ro"], timeout_seconds=2)
        executors.append(executor)
        return PriceQuery(llm.runnable, executor, today=lambda: TODAY), llm

    yield build
    for executor in executors:
        executor.engine.dispose()


def test_a_question_is_answered_end_to_end(make_engine):
    engine, _ = make_engine("SELECT c.name, COUNT(*) AS n FROM products p JOIN categories c ON c.id = p.category_id GROUP BY c.name ORDER BY n DESC")

    answer = engine.answer("How many products per category?")

    assert answer.result.rows[0] == ("Fiction", 14) and answer.attempts == 1


def test_a_mysql_error_message_reaches_the_retry_prompt(make_engine):
    engine, llm = make_engine("SELECT nope FROM products", "SELECT COUNT(*) FROM products")

    answer = engine.answer("How many products are there?")

    assert answer.attempts == 2 and answer.result.rows == [(60,)]
    assert "Unknown column 'nope'" in llm.prompts[1]


def test_unsafe_sql_is_refused_before_it_reaches_mysql(mysql, make_engine):
    engine, llm = make_engine("DROP TABLE products")

    with pytest.raises(guard.UnsafeSQL):
        engine.answer("Remove everything")

    assert llm.calls == 1 and count(mysql["admin"], "products") == 60


def test_the_rest_api_returns_mysql_rows_as_json(make_engine):
    engine, _ = make_engine("SELECT p.title, ph.price, ph.scraped_at FROM products p JOIN price_history ph ON ph.product_id = p.id ORDER BY ph.id LIMIT 2")
    client = create_app(engine).test_client()

    response = client.post("/api/ask", json={"question": "Show two prices"})
    body = response.get_json()

    assert response.status_code == 200 and client.get("/health").get_json()["dialect"] == "mysql"
    assert body["columns"] == ["title", "price", "scraped_at"] and body["row_count"] == 2
    assert isinstance(body["rows"][0][1], float) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", body["rows"][0][2])


def test_every_hand_written_answer_runs_and_returns_rows_on_mysql(mysql):
    import json

    from eval.run_eval import gold_only

    questions = json.loads((ROOT / "eval" / "questions.json").read_text())

    executor = make_readonly_engine(mysql["ro"], 5)
    try:
        assert gold_only(executor, questions) == 0
    finally:
        executor.engine.dispose()
