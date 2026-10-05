"""Build a small SYNTHETIC price database in the same four tables the scraper fills.

It is deterministic (fixed seed, fixed end date) so the hand-written answers in
eval/questions.json stay valid. It is not scraped data.

    python -m pricequery.seed                    # writes data/demo.db
    python -m pricequery.seed --url mysql+pymysql://user:pw@localhost/pricedb
"""
import argparse
import datetime
import random
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from . import config
from .models import Base, Category, PriceAlert, PriceHistory, Product

LAST_SCRAPE_DAY = datetime.date(2026, 9, 30)
DAYS = 90
# (category, number of products); deliberately unequal so "which has most" has one answer
CATEGORIES = [("Fiction", 14), ("Mystery", 12), ("Travel", 11), ("Science", 10), ("Poetry", 8), ("Cooking", 5)]
_ADJECTIVES = ["Silent", "Golden", "Hidden", "Broken", "Distant", "Crimson", "Quiet", "Endless", "Winter", "Paper",
               "Salt", "Midnight", "Hollow", "Bright", "Wandering", "Forgotten"]
_NOUNS = ["Harbour", "Garden", "Orchard", "Letters", "Compass", "Lantern", "Bridge", "Archive", "River", "Atlas",
          "Kitchen", "Mirror", "Voyage", "Clockwork", "Meadow", "Theory"]


def build_demo_data(session: Session, seed: int = 7) -> None:
    rng = random.Random(seed)
    titles = [f"The {a} {n}" for a in _ADJECTIVES for n in _NOUNS]
    rng.shuffle(titles)
    title_iter = iter(titles)

    products = []
    for name, count in CATEGORIES:
        category = Category(name=name)
        session.add(category)
        session.flush()
        for _ in range(count):
            n = len(products) + 1
            product = Product(upc=f"{n:012d}", title=next(title_iter), url=f"https://example.test/p/{n}", category_id=category.id)
            session.add(product)
            products.append(product)
    session.flush()

    first_day = LAST_SCRAPE_DAY - datetime.timedelta(days=DAYS - 1)
    for product in products:
        price = round(rng.uniform(8, 60), 2)
        in_stock = True
        for offset in range(DAYS):
            if rng.random() < 0.08:  # an occasional price move
                price = round(max(5.0, price * rng.uniform(0.75, 1.15)), 2)
            if rng.random() < 0.03:
                in_stock = not in_stock
            when = datetime.datetime.combine(first_day + datetime.timedelta(days=offset), datetime.time(6, 0))
            session.add(PriceHistory(product_id=product.id, price=price, in_stock=in_stock, scraped_at=when))
    session.flush()

    for i, product in enumerate(rng.sample(products, 15), start=1):
        triggered = rng.random() < 0.4
        session.add(
            PriceAlert(
                product_id=product.id,
                email=f"subscriber{i}@example.test",
                target_price=round(rng.uniform(8, 40), 2),
                triggered_at=datetime.datetime(2026, 9, rng.randint(1, 30), 6, 0) if triggered else None,
            )
        )


def create_demo_database(url: str) -> None:
    if url.startswith("sqlite:///"):
        db_file = Path(url[len("sqlite:///"):])
        db_file.parent.mkdir(parents=True, exist_ok=True)
        db_file.unlink(missing_ok=True)
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        build_demo_data(session)
        session.commit()
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=config.DATABASE_URL, help="database to (re)create; WARNING: drops the four tables")
    args = parser.parse_args()
    create_demo_database(args.url)
    print(f"seeded {args.url}")


if __name__ == "__main__":
    main()
