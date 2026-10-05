"""Worked examples shown to the model. Kept free of date functions so they hold for MySQL and SQLite."""

EXAMPLES = [
    (
        "How many products are in each category?",
        "SELECT c.name, COUNT(*) AS product_count\n"
        "FROM products p JOIN categories c ON c.id = p.category_id\n"
        "GROUP BY c.name ORDER BY product_count DESC",
    ),
    (
        "What is the current price of the product titled 'Example Title'?",
        "SELECT p.title, ph.price, ph.scraped_at\n"
        "FROM products p JOIN price_history ph ON ph.product_id = p.id\n"
        "WHERE p.title = 'Example Title'\n"
        "ORDER BY ph.scraped_at DESC LIMIT 1",
    ),
    (
        "Which products are out of stock right now?",
        "SELECT p.title\n"
        "FROM products p JOIN price_history ph ON ph.product_id = p.id\n"
        "WHERE ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)\n"
        "  AND ph.in_stock = 0",
    ),
    (
        "What was the average price in the Travel category during June 2026?",
        "SELECT AVG(ph.price) AS avg_price\n"
        "FROM price_history ph\n"
        "JOIN products p ON p.id = ph.product_id\n"
        "JOIN categories c ON c.id = p.category_id\n"
        "WHERE c.name = 'Travel' AND ph.scraped_at >= '2026-06-01' AND ph.scraped_at < '2026-07-01'",
    ),
]


def format_examples() -> str:
    return "\n\n".join(f"Question: {q}\nSQL:\n{sql}" for q, sql in EXAMPLES)
