"""Check generated SQL against hand-written SQL on a set of questions.

    python -m eval.run_eval --gold-only    # no model: confirm every hand-written query runs
    python -m eval.run_eval                # needs OPENAI_API_KEY: score the model's answers

For each question the model's query and the gold query both run on the same read-only
database, and the two result sets are compared strictly (see pricequery/evaluation.py).
Questions the schema cannot answer pass if the model replies NO_QUERY.
"""
import argparse
import csv
import json
from pathlib import Path

from pricequery import config, guard
from pricequery.evaluation import results_match
from pricequery.executor import QueryError, make_readonly_engine
from pricequery.nl2sql import CannotAnswer, PriceQuery

HERE = Path(__file__).resolve().parent


def run_gold(executor, question):
    safe = guard.prepare(question["gold_sql"], config.MAX_ROWS, config.BLOCKED_IDENTIFIERS)
    return executor.run(safe, config.MAX_ROWS)


def gold_only(executor, questions) -> int:
    failures = 0
    for q in questions["answerable"]:
        try:
            result = run_gold(executor, q)
            problem = "returned no rows" if not result.rows else "hit the row cap" if result.truncated else None
        except (guard.UnsafeSQL, QueryError) as exc:
            problem = str(exc)
        failures += 1 if problem else 0
        print(f"{q['id']}  {'FAIL: ' + problem if problem else 'ok'}")
    print(f"\n{len(questions['answerable']) - failures}/{len(questions['answerable'])} gold queries run and return rows")
    return failures


def score_model(executor, questions, llm, out_path: Path) -> None:
    engine = PriceQuery(llm, executor)
    rows = []
    for q in questions["answerable"]:
        gold = run_gold(executor, q)
        outcome, attempts, sql = "wrong", 0, ""
        try:
            answer = engine.answer(q["question"])
            attempts, sql = answer.attempts, answer.sql
            outcome = "correct" if results_match(answer.result.rows, gold.rows, q.get("ordered", False)) else "wrong"
        except CannotAnswer:
            outcome = "refused"
        except guard.UnsafeSQL:
            outcome = "unsafe"
        except QueryError:
            outcome = "sql_error"
        rows.append({"id": q["id"], "kind": "answerable", "outcome": outcome, "attempts": attempts, "sql": sql})
        print(f"{q['id']}  {outcome}")
    for q in questions["unanswerable"]:
        try:
            engine.answer(q["question"])
            outcome = "answered"  # it should have said NO_QUERY
        except CannotAnswer:
            outcome = "correct"
        except (guard.UnsafeSQL, QueryError):
            outcome = "wrong"
        rows.append({"id": q["id"], "kind": "unanswerable", "outcome": outcome, "attempts": 0, "sql": ""})
        print(f"{q['id']}  {outcome}")

    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    answerable = [r for r in rows if r["kind"] == "answerable"]
    correct = sum(r["outcome"] == "correct" for r in answerable)
    retried = sum(r["attempts"] > 1 for r in answerable)
    print(f"\nanswerable: {correct}/{len(answerable)} match the hand-written SQL ({retried} needed the retry)")
    unans = [r for r in rows if r["kind"] == "unanswerable"]
    print(f"unanswerable: {sum(r['outcome'] == 'correct' for r in unans)}/{len(unans)} correctly declined")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", type=Path, default=HERE / "questions.json")
    parser.add_argument("--gold-only", action="store_true")
    parser.add_argument("--out", type=Path, default=HERE / "results.csv")
    args = parser.parse_args()

    questions = json.loads(args.questions.read_text())
    executor = make_readonly_engine(config.DATABASE_URL, config.QUERY_TIMEOUT_SECONDS)
    if args.gold_only:
        raise SystemExit(1 if gold_only(executor, questions) else 0)

    from pricequery.cli import build_llm

    score_model(executor, questions, build_llm(), args.out)


if __name__ == "__main__":
    main()
