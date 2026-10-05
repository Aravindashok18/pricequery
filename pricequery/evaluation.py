"""Compare a generated query's result with a hand-written one."""
import datetime
import decimal
from typing import Iterable, List, Sequence


def _normalise(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (float, decimal.Decimal)):
        return round(float(value), 2)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _normalise_rows(rows: Iterable[Sequence]) -> List[tuple]:
    return [tuple(_normalise(v) for v in row) for row in rows]


def results_match(got: Iterable[Sequence], gold: Iterable[Sequence], ordered: bool = False) -> bool:
    """Strict execution match: same rows with the same values, floats compared to 2 decimals.

    Column names are ignored (the model may alias differently) but column count and order are
    not, so returning an extra column counts as a miss. Row order only matters when `ordered`.
    """
    got_rows, gold_rows = _normalise_rows(got), _normalise_rows(gold)
    if ordered:
        return got_rows == gold_rows
    key = lambda row: tuple((v is None, repr(v)) for v in row)
    return sorted(got_rows, key=key) == sorted(gold_rows, key=key)
