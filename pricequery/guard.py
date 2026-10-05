"""Checks applied to every generated statement before it reaches the database.

This is the second of three layers: the model is told to write SELECTs, this module
rejects anything that isn't a single read-only SELECT and caps the row count, and the
database connection itself is read-only (see executor.py and db/readonly_user.sql).
"""
import re
from typing import Iterable

import sqlparse
from sqlparse import tokens as T


class UnsafeSQL(ValueError):
    """The statement is not a plain, single, read-only SELECT."""


# Words that never belong in a read-only SELECT. Checked against real SQL tokens only,
# so a string literal like 'drop table' or a column called updated_at is fine.
_FORBIDDEN_WORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "CALL", "EXEC", "EXECUTE", "SET", "LOCK", "UNLOCK", "HANDLER", "LOAD",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "ANALYZE", "INTO", "OUTFILE", "DUMPFILE",
    "SHOW", "DESCRIBE", "EXPLAIN", "USE", "COMMIT", "ROLLBACK", "SAVEPOINT",
}
# Functions that read files, hold locks or stall the server.
_FORBIDDEN_FUNCTIONS = {
    "SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "PG_SLEEP", "LOAD_EXTENSION",
    "SYS_EXEC", "SYS_EVAL",
}

_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*(?:(,)\s*(\d+)|OFFSET\s+(\d+))?\s*$", re.IGNORECASE)


def _words(statement: sqlparse.sql.Statement) -> list:
    """Upper-cased keyword/identifier tokens, skipping strings, comments and punctuation."""
    words = []
    for token in statement.flatten():
        # "double quoted" text is an identifier in SQLite/ANSI SQL, so it must be checked like one
        quoted_identifier = token.ttype is T.String.Symbol
        if not quoted_identifier and (token.ttype in T.Comment or token.ttype in T.String or token.ttype in T.Whitespace):
            continue
        if token.ttype in T.Punctuation or token.ttype in T.Operator or token.ttype in T.Number:
            continue
        words.append(token.value.strip("`\"[] ").upper())
    return words


def validate_select(sql: str, blocked_identifiers: Iterable[str] = ()) -> str:
    """Return the statement without a trailing semicolon, or raise UnsafeSQL."""
    if not sql or not sql.strip():
        raise UnsafeSQL("empty statement")
    # MySQL runs code hidden in /*! ... */ comments, so these are rejected outright
    # rather than stripped like ordinary comments.
    if "/*!" in sql or "/*+" in sql:
        raise UnsafeSQL("executable comments are not allowed")

    statements = [s for s in sqlparse.parse(sql) if str(s).strip().strip(";").strip()]
    if len(statements) != 1:
        raise UnsafeSQL("exactly one statement is allowed")
    statement = statements[0]
    if statement.get_type() != "SELECT":
        raise UnsafeSQL(f"only SELECT statements are allowed (got {statement.get_type()})")

    words = _words(statement)
    for word in words:
        if word in _FORBIDDEN_WORDS:
            raise UnsafeSQL(f"{word} is not allowed")
    for i, word in enumerate(words):
        if word in _FORBIDDEN_FUNCTIONS:
            raise UnsafeSQL(f"{word}() is not allowed")
        if word == "FOR" and i + 1 < len(words) and words[i + 1] == "SHARE":
            raise UnsafeSQL("locking reads are not allowed")  # FOR UPDATE / LOCK IN SHARE MODE are caught above
    blocked = {b.lower() for b in blocked_identifiers}
    for word in words:
        if word.lower() in blocked:
            raise UnsafeSQL(f"column {word.lower()!r} is not available")

    # Re-render without comments and without the trailing ';'
    return sqlparse.format(sql, strip_comments=True).strip().rstrip(";").strip()


def apply_row_limit(sql: str, max_rows: int) -> str:
    """Make sure the statement cannot return more than max_rows rows.

    Appends LIMIT when there is none and lowers a larger one. (The executor also
    stops reading at max_rows + 1 rows, so this is about not making the database
    do the work, not the only cap.)
    """
    match = _TRAILING_LIMIT.search(sql)
    if not match:
        return f"{sql} LIMIT {max_rows}"
    count, comma, count_after_comma, offset = match.groups()
    if comma:  # MySQL "LIMIT offset, count"
        capped = min(int(count_after_comma), max_rows)
        return f"{sql[: match.start()]}LIMIT {count}, {capped}"
    capped = min(int(count), max_rows)
    tail = f" OFFSET {offset}" if offset is not None else ""
    return f"{sql[: match.start()]}LIMIT {capped}{tail}"


def prepare(sql: str, max_rows: int, blocked_identifiers: Iterable[str] = ()) -> str:
    """Validate, then cap at max_rows + 1: the extra row is how the executor can tell the
    result was cut short, instead of silently showing exactly max_rows rows."""
    return apply_row_limit(validate_select(sql, blocked_identifiers), max_rows + 1)
