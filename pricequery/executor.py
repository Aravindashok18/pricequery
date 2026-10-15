"""Run a guarded statement on a read-only connection and cap what comes back."""
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from . import config


class QueryError(Exception):
    """The database rejected or failed the query. `retryable` means a corrected query might work."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class QueryResult:
    columns: List[str]
    rows: List[tuple]
    truncated: bool = False  # more rows existed than max_rows
    sql: str = ""
    extra: dict = field(default_factory=dict)


def make_readonly_engine(url: str, timeout_seconds: float = 5.0, pool_size: int = config.DB_POOL_SIZE) -> "ReadOnlyExecutor":
    return ReadOnlyExecutor(url, timeout_seconds, pool_size)


class ReadOnlyExecutor:
    """Owns an engine whose connections cannot write, whatever SQL is sent.

    SQLite: the file is opened with mode=ro and PRAGMA query_only=ON, and a progress
    handler aborts queries that run past the timeout.
    MySQL: sessions are set READ ONLY with a MAX_EXECUTION_TIME. This is a second line of
    defence behind the SELECT-only database user in db/readonly_user.sql, not a replacement.
    """

    def __init__(self, url: str, timeout_seconds: float = 5.0, pool_size: int = config.DB_POOL_SIZE):
        self.timeout_seconds = timeout_seconds
        self._local = threading.local()
        parsed = make_url(url)
        self.dialect = parsed.get_backend_name()
        if self.dialect == "sqlite" and parsed.database and parsed.database != ":memory:":
            parsed = make_url(f"sqlite:///file:{parsed.database}?mode=ro&uri=true")
        pool = {"pool_size": pool_size, "max_overflow": 0} if self.dialect != "sqlite" else {}
        self.engine: Engine = create_engine(parsed, pool_pre_ping=True, **pool)
        self._install_session_guards()

    def _install_session_guards(self) -> None:
        if self.dialect == "sqlite":
            @event.listens_for(self.engine, "connect")
            def sqlite_connect(dbapi_conn, _record):
                dbapi_conn.execute("PRAGMA query_only = ON")

                def abort_when_overdue():
                    deadline = getattr(self._local, "deadline", None)
                    return 1 if deadline is not None and time.monotonic() > deadline else 0

                dbapi_conn.set_progress_handler(abort_when_overdue, 10000)

        elif self.dialect == "mysql":
            @event.listens_for(self.engine, "connect")
            def mysql_connect(dbapi_conn, _record):
                cursor = dbapi_conn.cursor()
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(self.timeout_seconds * 1000)}")
                cursor.close()

    def run(self, sql: str, max_rows: int) -> QueryResult:
        # text() would read ":word" as a bind parameter, so colons are escaped; it also
        # takes care of doubling "%" for MySQL drivers.
        statement = text(sql.replace(":", r"\:"))
        self._local.deadline = time.monotonic() + self.timeout_seconds
        try:
            with self.engine.connect() as conn:
                result = conn.execute(statement)
                fetched = result.fetchmany(max_rows + 1)
                columns = list(result.keys())
        except DBAPIError as exc:
            message = str(getattr(exc, "orig", exc))
            raise QueryError(message, retryable=not _is_timeout(message)) from exc
        except SQLAlchemyError as exc:
            raise QueryError(str(exc)) from exc
        finally:
            self._local.deadline = None
        return QueryResult(
            columns=columns,
            rows=[tuple(row) for row in fetched[:max_rows]],
            truncated=len(fetched) > max_rows,
            sql=sql,
        )


def _is_timeout(message: str) -> bool:
    lowered = message.lower()
    return "interrupted" in lowered or "max_execution_time" in lowered or "execution time exceeded" in lowered
