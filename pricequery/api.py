"""A small Flask REST API in front of PriceQuery.

    python -m pricequery.api                          # http://127.0.0.1:5060
    gunicorn "pricequery.api:create_app()"            # behind a real server

    curl localhost:5060/api/ask -H 'Content-Type: application/json' \\
         -d '{"question": "Which category has the highest average price right now?"}'

Every answer comes from the same pipeline as the CLI: the model writes one SELECT, the guard
checks it, and a read-only connection runs it. The API only translates the outcomes into HTTP.
"""
from __future__ import annotations

import datetime
import decimal
import logging
from typing import Optional

from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider

from . import config, guard
from .executor import QueryError, make_readonly_engine
from .nl2sql import CannotAnswer, PriceQuery

MAX_QUESTION_CHARS = 500
log = logging.getLogger(__name__)


class JSONProvider(DefaultJSONProvider):
    """MySQL returns Decimal and datetime values; send numbers as numbers and times as ISO 8601."""

    @staticmethod
    def default(value):
        if isinstance(value, decimal.Decimal):
            return float(value)
        if isinstance(value, (datetime.datetime, datetime.date)):
            return value.isoformat()
        return DefaultJSONProvider.default(value)


def error(status: int, code: str, message: str):
    return jsonify(error=code, message=message), status


def create_app(engine: Optional[PriceQuery] = None) -> Flask:
    """Build the app. Without an `engine`, one is built from the environment (database URL and model settings)."""
    if engine is None:
        from .cli import build_llm  # imported here: it exits with a helpful message when no model is configured

        engine = PriceQuery(build_llm(), make_readonly_engine(config.DATABASE_URL, config.QUERY_TIMEOUT_SECONDS))

    app = Flask(__name__)
    app.json = JSONProvider(app)
    app.json.sort_keys = False

    @app.get("/health")
    def health():
        return jsonify(status="ok", dialect=engine.executor.dialect, model=config.LLM_MODEL, max_rows=engine.max_rows)

    @app.get("/api/schema")
    def schema():
        return jsonify(schema=engine.schema)

    @app.post("/api/ask")
    def ask():
        payload = request.get_json(silent=True)
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return error(400, "bad_request", 'Send JSON like {"question": "How many products are there?"}.')
        if len(question) > MAX_QUESTION_CHARS:
            return error(400, "bad_request", f"Questions are limited to {MAX_QUESTION_CHARS} characters.")

        try:
            answer = engine.answer(question.strip())
        except CannotAnswer:
            return error(422, "cannot_answer", "The database does not contain what that question asks for.")
        except guard.UnsafeSQL as exc:
            # nothing was run: the guard refused the model's statement
            return error(422, "unsafe_sql", f"The generated query was refused: {exc}")
        except QueryError as exc:
            if not exc.retryable:  # a timeout; asking the model again would not help
                return error(504, "timeout", "The query ran too long and was stopped.")
            log.warning("query failed after the retry: %s", exc)
            return error(502, "query_failed", "The generated query failed twice. Try rephrasing the question.")
        except Exception:  # the model service is down, rate-limited or misconfigured
            log.exception("model call failed")
            return error(503, "model_unavailable", "The language model could not be reached.")

        result = answer.result
        return jsonify(
            question=answer.question, sql=answer.sql, columns=result.columns,
            rows=[list(row) for row in result.rows], row_count=len(result.rows),
            truncated=result.truncated, attempts=answer.attempts,
        )

    @app.errorhandler(404)
    def not_found(_):
        return error(404, "not_found", "No such route.")

    @app.errorhandler(405)
    def wrong_method(_):
        return error(405, "method_not_allowed", "That route does not accept this method.")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5060)
