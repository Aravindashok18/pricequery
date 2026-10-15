"""Plain-English question -> guarded SQL -> rows, with one retry when the database objects."""
import datetime
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from . import config, guard
from .examples import format_examples
from .executor import QueryError, QueryResult, ReadOnlyExecutor
from .schema import describe_schema

NO_QUERY = "NO_QUERY"

_SYSTEM = (
    "You translate questions about a product price-tracking database into one {dialect} SELECT query.\n"
    "\n"
    "Schema:\n{schema}\n"
    "\n"
    "Rules:\n"
    "- Write exactly one SELECT statement (a WITH ... SELECT is fine). Never modify data.\n"
    "- Use only the tables and columns listed above.\n"
    "- Today's date is {today}. Use literal dates, not date functions, for relative periods.\n"
    "- Prices are in GBP. For 'current'/'latest' price use each product's row with the latest scraped_at.\n"
    "- Reply with the SQL only: no explanation, no markdown fences.\n"
    f"- If the schema cannot answer the question, reply with exactly {NO_QUERY}.\n"
    "\n"
    "Examples:\n{examples}"
)

_ASK = ChatPromptTemplate.from_messages([("system", _SYSTEM), ("human", "{question}")])
_FIX = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM),
        ("human", "{question}"),
        ("ai", "{previous_sql}"),
        ("human", "That query failed with this database error:\n{error}\nReply with only the corrected SQL query."),
    ]
)

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class CannotAnswer(Exception):
    """The model decided the schema cannot answer the question."""


def extract_sql(raw: str) -> str:
    """Pull the statement out of a model reply, tolerating markdown fences."""
    match = _FENCE.search(raw)
    return (match.group(1) if match else raw).strip()


@dataclass
class Answer:
    question: str
    sql: str  # the statement that actually ran (after the guard added/lowered the LIMIT)
    result: QueryResult
    attempts: int
    history: List[dict] = field(default_factory=list)  # every model attempt: sql + error if any


class PriceQuery:
    def __init__(
        self,
        llm,
        executor: ReadOnlyExecutor,
        max_rows: int = config.MAX_ROWS,
        blocked_identifiers=config.BLOCKED_IDENTIFIERS,
        today: Optional[Callable[[], datetime.date]] = None,
    ):
        self.executor = executor
        self.max_rows = max_rows
        self.blocked = tuple(blocked_identifiers)
        self._today = today or datetime.date.today
        self._parser = StrOutputParser()
        self._ask = _ASK | llm | self._parser
        self._fix = _FIX | llm | self._parser
        self._schema = describe_schema(executor.engine, self.blocked)

    @property
    def schema(self) -> str:
        """The schema text the model is shown (blocked columns already left out)."""
        return self._schema

    def _context(self) -> dict:
        return {
            "dialect": self.executor.dialect,
            "schema": self._schema,
            "today": self._today().isoformat(),
            "examples": format_examples(),
        }

    def answer(self, question: str) -> Answer:
        context = {**self._context(), "question": question}
        raw = self._ask.invoke(context)
        history: List[dict] = []

        for attempt in (1, 2):
            if raw.strip().upper().startswith(NO_QUERY):
                raise CannotAnswer(question)
            sql = extract_sql(raw)
            # An unsafe statement is refused outright; asking the model to "fix" it would just
            # give it another try at getting something past the guard.
            safe_sql = guard.prepare(sql, self.max_rows, self.blocked)
            try:
                result = self.executor.run(safe_sql, self.max_rows)
            except QueryError as exc:
                history.append({"sql": safe_sql, "error": str(exc)})
                if attempt == 2 or not exc.retryable:
                    raise
                raw = self._fix.invoke({**context, "previous_sql": sql, "error": str(exc)})
                continue
            history.append({"sql": safe_sql, "error": None})
            return Answer(question, safe_sql, result, attempt, history)
        raise AssertionError("unreachable")  # pragma: no cover
