"""Print the MySQL DDL for the four tables (generated from models.py; nothing connects anywhere).

    python -m pricequery.ddl > db/schema.sql
"""
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from .models import Base


def mysql_ddl() -> str:
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=mysql.dialect())).strip() + ";")
        statements.extend(str(CreateIndex(ix).compile(dialect=mysql.dialect())).strip() + ";" for ix in sorted(table.indexes, key=lambda i: i.name))
    return "\n\n".join(statements) + "\n"


if __name__ == "__main__":
    print(mysql_ddl(), end="")
