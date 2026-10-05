import hashlib

from sqlalchemy import create_engine, text

from pricequery.schema import describe_schema
from pricequery.seed import create_demo_database


def test_schema_description_lists_tables_keys_and_notes_and_hides_blocked_columns(executor):
    schema = describe_schema(executor.engine, ("email",))
    assert "TABLE price_history" in schema and "append-only" in schema
    assert "product_id INTEGER REFERENCES products.id" in schema
    assert "price in GBP" in schema
    assert "email" not in schema


def test_schema_shows_a_column_when_it_is_not_blocked(executor):
    assert "email" in describe_schema(executor.engine, ())


def _fingerprint(url):
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT product_id, price, in_stock, scraped_at FROM price_history ORDER BY id")).fetchall()
        titles = conn.execute(text("SELECT title FROM products ORDER BY id")).fetchall()
    engine.dispose()
    return hashlib.sha256(repr((rows, titles)).encode()).hexdigest(), len(rows), len(titles)


def test_demo_database_is_deterministic_and_has_the_documented_shape(tmp_path):
    a, b = f"sqlite:///{tmp_path / 'a.db'}", f"sqlite:///{tmp_path / 'b.db'}"
    create_demo_database(a)
    create_demo_database(b)
    fp_a, fp_b = _fingerprint(a), _fingerprint(b)
    assert fp_a == fp_b
    assert fp_a[1:] == (5400, 60)  # 60 products x 90 days
