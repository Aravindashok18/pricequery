import datetime
import decimal
import json
from pathlib import Path

import pytest

from pricequery.evaluation import results_match
from pricequery.guard import prepare
from pricequery.nl2sql import PriceQuery

QUESTIONS = json.loads((Path(__file__).resolve().parent.parent / "eval" / "questions.json").read_text())


def test_row_order_is_ignored_unless_asked_for():
    assert results_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")])
    assert not results_match([(1, "a"), (2, "b")], [(2, "b"), (1, "a")], ordered=True)


def test_floats_decimals_and_bools_are_compared_sensibly():
    assert results_match([(19.999,)], [(decimal.Decimal("20.00"),)])
    assert results_match([(True,)], [(1,)])
    assert results_match([(datetime.datetime(2026, 9, 1, 6, 0),)], [("2026-09-01T06:00:00",)])


@pytest.mark.parametrize(
    "got, gold",
    [
        ([(1,)], [(2,)]),
        ([(1, "extra")], [(1,)]),  # an extra column is a miss
        ([(1,), (1,)], [(1,)]),  # duplicate rows matter
        ([], [(1,)]),
        ([(None,)], [(0,)]),
    ],
)
def test_mismatches_are_detected(got, gold):
    assert not results_match(got, gold)


def test_none_values_sort_without_error():
    assert results_match([(None,), (1,)], [(1,), (None,)])


def test_every_gold_query_passes_the_guard_runs_and_returns_rows(executor):
    for q in QUESTIONS["answerable"]:
        result = executor.run(prepare(q["gold_sql"], 100, ("email",)), 100)
        assert result.rows, q["id"]
        assert not result.truncated, q["id"]


def test_harness_scores_a_model_that_returns_the_gold_sql_as_fully_correct(executor):
    from tests.conftest import ScriptedLLM, TODAY

    for q in QUESTIONS["answerable"]:
        llm = ScriptedLLM(q["gold_sql"])
        engine = PriceQuery(llm.runnable, executor, today=lambda: TODAY)
        gold = executor.run(prepare(q["gold_sql"], 100, ("email",)), 100)
        assert results_match(engine.answer(q["question"]).result.rows, gold.rows, q.get("ordered", False)), q["id"]


def test_harness_marks_a_plausible_but_wrong_query_as_incorrect(executor):
    from tests.conftest import ScriptedLLM, TODAY

    q = next(q for q in QUESTIONS["answerable"] if q["id"] == "q08")  # products out of stock right now
    wrong = "SELECT COUNT(DISTINCT product_id) FROM price_history WHERE in_stock = 0"  # ever, not "right now"
    engine = PriceQuery(ScriptedLLM(wrong).runnable, executor, today=lambda: TODAY)
    gold = executor.run(prepare(q["gold_sql"], 100, ("email",)), 100)
    assert not results_match(engine.answer(q["question"]).result.rows, gold.rows)
