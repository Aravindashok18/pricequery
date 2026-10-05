"""Ask questions from the terminal.

    python -m pricequery.cli "Which category has the highest average price right now?"
    python -m pricequery.cli                       # interactive prompt
    python -m pricequery.cli --sql "SELECT COUNT(*) FROM products"   # no model: run SQL through the same guard
"""
import argparse
import sys

from . import config, guard
from .executor import QueryError, QueryResult, make_readonly_engine
from .nl2sql import CannotAnswer, PriceQuery


def format_table(result: QueryResult) -> str:
    cells = [[("NULL" if v is None else str(v)) for v in row] for row in result.rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(result.columns)]
    line = lambda parts: "  ".join(p.ljust(w) for p, w in zip(parts, widths)).rstrip()
    out = [line(result.columns), line(["-" * w for w in widths])] + [line(r) for r in cells]
    out.append(f"({len(result.rows)} row{'s' if len(result.rows) != 1 else ''}"
               + (f", truncated at {len(result.rows)}" if result.truncated else "") + ")")
    return "\n".join(out)


def build_llm():
    if not config.OPENAI_API_KEY:
        sys.exit("Set OPENAI_API_KEY (and pip install -r requirements-llm.txt) to turn questions into SQL, "
                 "or use --sql to run a SELECT directly.")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        sys.exit("langchain-openai is not installed: pip install -r requirements-llm.txt")
    return ChatOpenAI(model=config.LLM_MODEL, temperature=0)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="a question in plain English")
    parser.add_argument("--sql", help="skip the model and run this statement through the guard + read-only connection")
    parser.add_argument("--show-sql", action="store_true", help="print the SQL that ran")
    args = parser.parse_args(argv)

    executor = make_readonly_engine(config.DATABASE_URL, config.QUERY_TIMEOUT_SECONDS)

    if args.sql:
        try:
            safe = guard.prepare(args.sql, config.MAX_ROWS, config.BLOCKED_IDENTIFIERS)
            print(format_table(executor.run(safe, config.MAX_ROWS)))
        except guard.UnsafeSQL as exc:
            sys.exit(f"refused: {exc}")
        except QueryError as exc:
            sys.exit(f"database error: {exc}")
        return

    engine = PriceQuery(build_llm(), executor)
    questions = [args.question] if args.question else iter(lambda: input("question> ").strip(), "quit")
    for question in questions:
        if not question:
            continue
        try:
            answer = engine.answer(question)
        except CannotAnswer:
            print("The database has no data that answers that.")
        except guard.UnsafeSQL as exc:
            print(f"Refused: the generated query was not a safe SELECT ({exc}).")
        except QueryError as exc:
            print(f"The query failed twice: {exc}")
        else:
            if args.show_sql:
                print(answer.sql, end="\n\n")
            print(format_table(answer.result))
            if answer.attempts > 1:
                print("(needed a second attempt after a SQL error)")


if __name__ == "__main__":
    main()
