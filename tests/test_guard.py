import pytest

from pricequery.guard import UnsafeSQL, apply_row_limit, prepare, validate_select

UNSAFE = [
    "DROP TABLE products",
    "DELETE FROM products",
    "UPDATE products SET title = 'x'",
    "INSERT INTO categories (name) VALUES ('x')",
    "SELECT 1; DROP TABLE products",
    "SELECT 1;DELETE FROM products;",
    "SELECT 1 -- harmless\n; DROP TABLE products",
    "SELECT * FROM products INTO OUTFILE '/tmp/x'",
    "SELECT SLEEP(10)",
    "SELECT BENCHMARK(1000000, MD5('a'))",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT * FROM products FOR UPDATE",
    "SELECT * FROM products LOCK IN SHARE MODE",
    "/*!50000 DROP TABLE products */ SELECT 1",
    "WITH x AS (SELECT 1) DELETE FROM products",
    "PRAGMA table_info(products)",
    "ATTACH DATABASE '/tmp/a.db' AS a",
    "SELECT load_extension('x')",
    "SHOW TABLES",
    "EXPLAIN SELECT 1",
    "",
    "  ;  ",
]


@pytest.mark.parametrize("sql", UNSAFE)
def test_unsafe_statements_are_rejected(sql):
    with pytest.raises(UnsafeSQL):
        validate_select(sql, blocked_identifiers=("email",))


@pytest.mark.parametrize("sql", ["SELECT email FROM price_alerts", "SELECT `Email` FROM price_alerts", 'SELECT "email" FROM price_alerts'])
def test_blocked_columns_are_rejected_however_they_are_quoted(sql):
    with pytest.raises(UnsafeSQL, match="email"):
        validate_select(sql, blocked_identifiers=("email",))


SAFE = [
    "SELECT COUNT(*) FROM products",
    "select title from products where title = 'drop table; delete from x'",  # keywords inside a string literal
    "SELECT title FROM products WHERE title LIKE '%set%' AND title <> 'a;b'",  # ';' and 'set' inside strings
    "WITH r AS (SELECT product_id, MAX(scraped_at) m FROM price_history GROUP BY product_id) SELECT * FROM r;",
    "SELECT REPLACE(title, 'a', 'b') FROM products",  # REPLACE() the function is fine
    "SELECT 1 UNION SELECT 2",
    "SELECT created_at, updated_at FROM t",  # words that merely contain forbidden ones
]


@pytest.mark.parametrize("sql", SAFE)
def test_plain_selects_pass(sql):
    assert validate_select(sql, blocked_identifiers=("email",))


def test_trailing_semicolon_and_comments_are_stripped():
    assert validate_select("SELECT 1 FROM products; ") == "SELECT 1 FROM products"
    assert validate_select("SELECT 1 FROM products -- why not") == "SELECT 1 FROM products"


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT * FROM p", "SELECT * FROM p LIMIT 100"),
        ("SELECT * FROM p LIMIT 5000", "SELECT * FROM p LIMIT 100"),
        ("SELECT * FROM p LIMIT 10", "SELECT * FROM p LIMIT 10"),
        ("SELECT * FROM p LIMIT 500 OFFSET 20", "SELECT * FROM p LIMIT 100 OFFSET 20"),
        ("SELECT * FROM p LIMIT 5, 500", "SELECT * FROM p LIMIT 5, 100"),
        ("SELECT * FROM (SELECT * FROM p LIMIT 3) x", "SELECT * FROM (SELECT * FROM p LIMIT 3) x LIMIT 100"),
        ("SELECT * FROM p limit 7", "SELECT * FROM p LIMIT 7"),
    ],
)
def test_row_limit_is_added_or_lowered_never_raised(sql, expected):
    assert apply_row_limit(sql, 100) == expected


def test_prepare_validates_then_limits():
    # one more than the cap, so the executor can tell when rows were cut off
    assert prepare("SELECT * FROM products;", 25) == "SELECT * FROM products LIMIT 26"
    with pytest.raises(UnsafeSQL):
        prepare("DELETE FROM products", 25)
