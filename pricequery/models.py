"""The four tables the scraper (ecommerce-price-tracker) writes. PriceQuery only ever reads them."""
import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False, unique=True)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    upc = Column(String(64), nullable=False, unique=True)
    title = Column(String(255), nullable=False)
    url = Column(String(500), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    price = Column(Float, nullable=False)
    in_stock = Column(Boolean, nullable=False, default=True)
    scraped_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_price_history_product_id_scraped_at", "product_id", "scraped_at"),)


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    email = Column(String(255), nullable=False)
    target_price = Column(Float, nullable=False)
    triggered_at = Column(DateTime, nullable=True)

    __table_args__ = (UniqueConstraint("product_id", "email", "target_price", name="uq_alert_product_email_target"),)
