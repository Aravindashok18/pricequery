"""Describe the live database to the model: tables, columns, keys and what the columns mean."""
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# Plain-English hints for the columns the scraper writes. Only used for columns that exist.
COLUMN_NOTES = {
    "categories.name": "category name, e.g. 'Travel'",
    "products.upc": "unique product code",
    "products.title": "product title",
    "products.url": "product page URL",
    "price_history.price": "price in GBP at the moment of that scrape",
    "price_history.in_stock": "1 if in stock at that scrape, 0 if not",
    "price_history.scraped_at": "when the price was scraped; one row per product per scrape",
    "price_alerts.target_price": "price at or below which the subscriber is alerted",
    "price_alerts.triggered_at": "when the alert fired; NULL if it has not fired yet",
}
TABLE_NOTES = {
    "price_history": "append-only time series; the CURRENT price of a product is its row with the latest scraped_at",
}


def describe_schema(engine: Engine, blocked_identifiers: Iterable[str] = ()) -> str:
    blocked = {b.lower() for b in blocked_identifiers}
    tables = _mysql_structure(engine) if engine.dialect.name == "mysql" else _reflected_structure(engine)
    lines = []
    for table, columns in sorted(tables.items()):
        header = f"TABLE {table}"
        if table in TABLE_NOTES:
            header += f"  -- {TABLE_NOTES[table]}"
        lines.append(header)
        for name, column_type, is_pk, reference in columns:
            if name.lower() in blocked:
                continue  # never show the model columns it may not query
            parts = [f"  {name} {column_type}"]
            if is_pk:
                parts.append("PRIMARY KEY")
            if reference:
                parts.append(f"REFERENCES {reference}")
            note = COLUMN_NOTES.get(f"{table}.{name}")
            if note:
                parts.append(f"-- {note}")
            lines.append(" ".join(parts))
        lines.append("")
    return "\n".join(lines).strip()


def _reflected_structure(engine: Engine) -> Dict[str, List[Tuple[str, str, bool, Optional[str]]]]:
    """{table: [(column, type, is_primary_key, "table.column" it references or None)]} via SQLAlchemy reflection."""
    inspector = inspect(engine)
    tables = {}
    for table in inspector.get_table_names():
        pk = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
        fks = {
            fk["constrained_columns"][0]: f"{fk['referred_table']}.{fk['referred_columns'][0]}"
            for fk in inspector.get_foreign_keys(table)
        }
        tables[table] = [(c["name"], str(c["type"]), c["name"] in pk, fks.get(c["name"])) for c in inspector.get_columns(table)]
    return tables


def _mysql_structure(engine: Engine) -> Dict[str, List[Tuple[str, str, bool, Optional[str]]]]:
    """The same structure read from information_schema.

    SQLAlchemy reflects MySQL tables with SHOW CREATE TABLE, which MySQL refuses to a user who has only
    column-level SELECT grants (like the read-only user in db/readonly_user.sql, which cannot read
    price_alerts.email). information_schema lists exactly the columns the user may see, so the model
    is told about those and no others.
    """
    def text_of(value) -> str:
        return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)

    with engine.connect() as conn:
        database = conn.execute(text("SELECT DATABASE()")).scalar_one()
        columns = conn.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :db ORDER BY TABLE_NAME, ORDINAL_POSITION"), {"db": database}).all()
        references = conn.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = :db AND REFERENCED_TABLE_NAME IS NOT NULL"), {"db": database}).all()
    fk = {(text_of(t), text_of(c)): f"{text_of(rt)}.{text_of(rc)}" for t, c, rt, rc in references}
    tables: Dict[str, list] = {}
    for table, column, column_type, key in columns:
        table, column = text_of(table), text_of(column)
        tables.setdefault(table, []).append((column, text_of(column_type).upper(), text_of(key) == "PRI", fk.get((table, column))))
    return tables
