import datetime
import decimal
import json

import pytest
from flask import Flask
from langchain_core.runnables import RunnableLambda

from pricequery.api import MAX_QUESTION_CHARS, JSONProvider, create_app
from pricequery.nl2sql import PriceQuery
from tests.conftest import TODAY


@pytest.fixture()
def api(make_engine):
    """Returns (client, llm): an app around an engine whose model replays the given replies."""
    def build(*replies, **kwargs):
        engine, llm = make_engine(*replies, **kwargs)
        return create_app(engine).test_client(), llm

    return build


def ask(client, question="How many products are there?"):
    return client.post("/api/ask", json={"question": question})


def product_count(executor):
    return executor.run("SELECT COUNT(*) FROM products", 10).rows[0][0]


# --- answers ---------------------------------------------------------------------------------

def test_a_question_returns_the_sql_that_ran_and_its_rows(api):
    client, _ = api("SELECT COUNT(*) AS n FROM products")

    response = ask(client)
    body = response.get_json()

    assert response.status_code == 200
    assert body["columns"] == ["n"] and body["rows"] == [[60]] and body["row_count"] == 1
    assert body["truncated"] is False and body["attempts"] == 1
    assert body["question"] == "How many products are there?"
    assert body["sql"].upper().startswith("SELECT COUNT(*)") and "LIMIT" in body["sql"].upper()  # the guard capped it


def test_a_join_across_tables_comes_back_as_json_rows(api):
    client, _ = api("SELECT c.name, COUNT(*) AS products FROM products p JOIN categories c ON c.id = p.category_id GROUP BY c.name ORDER BY c.name")

    body = ask(client, "How many products per category?").get_json()

    assert body["columns"] == ["name", "products"] and body["row_count"] == 6
    assert sum(row[1] for row in body["rows"]) == 60


def test_a_database_error_is_retried_once_and_reported(api):
    client, llm = api("SELECT COUNT(*) FROM product", "SELECT COUNT(*) FROM products")

    body = ask(client).get_json()

    assert body["attempts"] == 2 and body["rows"] == [[60]] and llm.calls == 2


def test_results_are_cut_at_the_row_cap_and_flagged(api):
    client, _ = api("SELECT title FROM products", max_rows=3)

    body = ask(client).get_json()

    assert body["row_count"] == 3 and body["truncated"] is True


# --- refusals ---------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE products", "DELETE FROM products", "UPDATE products SET title = 'x'",
    "SELECT 1; DROP TABLE products", "SELECT * FROM products INTO OUTFILE '/tmp/x'",
])
def test_destructive_or_unsafe_statements_are_refused_and_the_data_is_untouched(api, executor, sql):
    client, llm = api(sql)

    response = ask(client)

    assert response.status_code == 422 and response.get_json()["error"] == "unsafe_sql"
    assert llm.calls == 1  # an unsafe statement is not sent back to the model for another try
    assert product_count(executor) == 60


def test_a_query_that_reads_the_blocked_email_column_is_refused(api):
    client, _ = api("SELECT email FROM price_alerts")

    assert ask(client, "Who has alerts?").get_json()["error"] == "unsafe_sql"


def test_the_model_saying_the_schema_cannot_answer_becomes_a_422(api):
    client, _ = api("NO_QUERY")

    response = ask(client, "What is the weather in Paris?")

    assert response.status_code == 422 and response.get_json()["error"] == "cannot_answer"


def test_a_query_that_fails_twice_is_a_502(api):
    client, llm = api("SELECT nope FROM products", "SELECT still_nope FROM products")

    response = ask(client)

    assert response.status_code == 502 and response.get_json()["error"] == "query_failed" and llm.calls == 2


def test_a_runaway_query_is_stopped_by_the_timeout_and_is_a_504(api):
    runaway = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n")
    client, llm = api(runaway)

    response = ask(client)

    assert response.status_code == 504 and response.get_json()["error"] == "timeout" and llm.calls == 1


def test_a_model_outage_is_a_503_without_leaking_the_error(executor):
    def down(_):
        raise ConnectionError("api.example.com: secret-token-123 rejected")

    client = create_app(PriceQuery(RunnableLambda(down), executor, today=lambda: TODAY)).test_client()

    response = ask(client)

    assert response.status_code == 503 and response.get_json()["error"] == "model_unavailable"
    assert "secret-token" not in response.get_data(as_text=True)


# --- input checking -----------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [None, [], "question", {}, {"question": ""}, {"question": "   "}, {"question": 7}, {"q": "x"}])
def test_malformed_requests_get_a_400_and_never_reach_the_model(api, payload):
    client, llm = api()

    response = client.post("/api/ask", json=payload) if payload is not None else client.post("/api/ask", data="not json", content_type="text/plain")

    assert response.status_code == 400 and response.get_json()["error"] == "bad_request" and llm.calls == 0


def test_overlong_questions_are_rejected(api):
    client, llm = api()

    response = ask(client, "x" * (MAX_QUESTION_CHARS + 1))

    assert response.status_code == 400 and llm.calls == 0


def test_unknown_routes_and_methods_answer_in_json(api):
    client, _ = api()

    assert client.get("/nope").get_json()["error"] == "not_found"
    assert client.get("/api/ask").status_code == 405 and client.get("/api/ask").get_json()["error"] == "method_not_allowed"


# --- the other routes ---------------------------------------------------------------------------

def test_health_reports_the_database_and_model(api):
    client, _ = api()

    body = client.get("/health").get_json()

    assert body["status"] == "ok" and body["dialect"] == "sqlite" and body["max_rows"] == 100 and body["model"]


def test_schema_route_lists_tables_and_hides_the_blocked_column(api):
    client, _ = api()

    schema = client.get("/api/schema").get_json()["schema"]

    assert "TABLE price_history" in schema and "TABLE products" in schema
    assert "email" not in schema


def test_decimals_and_datetimes_serialise_as_numbers_and_iso_strings():
    provider = JSONProvider(Flask(__name__))

    def roundtrip(value):
        return json.loads(provider.dumps({"v": value}))["v"]

    assert roundtrip(decimal.Decimal("12.50")) == 12.5
    assert roundtrip(datetime.datetime(2026, 10, 1, 9, 30)) == "2026-10-01T09:30:00"
    assert roundtrip(datetime.date(2026, 10, 1)) == "2026-10-01"


def test_the_app_refuses_to_start_without_a_model_configured(monkeypatch):
    monkeypatch.setattr("pricequery.config.OPENAI_API_KEY", "")

    with pytest.raises(SystemExit):
        create_app()
