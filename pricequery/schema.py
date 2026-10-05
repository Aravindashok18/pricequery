"""Describe the live database to the model: tables, columns, keys and what the columns mean."""
from typing import Iterable

from sqlalchemy import inspect
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
    inspector = inspect(engine)
    lines = []
    for table in sorted(inspector.get_table_names()):
        pk = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
        fks = {
            fk["constrained_columns"][0]: f"{fk['referred_table']}.{fk['referred_columns'][0]}"
            for fk in inspector.get_foreign_keys(table)
        }
        header = f"TABLE {table}"
        if table in TABLE_NOTES:
            header += f"  -- {TABLE_NOTES[table]}"
        lines.append(header)
        for column in inspector.get_columns(table):
            name = column["name"]
            if name.lower() in blocked:
                continue  # never show the model columns it may not query
            parts = [f"  {name} {column['type']}"]
            if name in pk:
                parts.append("PRIMARY KEY")
            if name in fks:
                parts.append(f"REFERENCES {fks[name]}")
            note = COLUMN_NOTES.get(f"{table}.{name}")
            if note:
                parts.append(f"-- {note}")
            lines.append(" ".join(parts))
        lines.append("")
    return "\n".join(lines).strip()
