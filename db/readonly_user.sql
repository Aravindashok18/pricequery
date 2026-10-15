-- Run once as an administrator. PriceQuery should connect as this user, not as the account the
-- scraper writes with, so the database itself refuses anything but SELECT.
-- Replace the database name and choose your own password. MAX_USER_CONNECTIONS must stay above
-- (app processes x DB_POOL_SIZE); each process keeps at most DB_POOL_SIZE connections open.

CREATE USER 'pricequery_ro'@'localhost' IDENTIFIED BY '<choose-a-password>'
  WITH MAX_USER_CONNECTIONS 20 MAX_QUERIES_PER_HOUR 5000;

GRANT SELECT ON pricedb.categories    TO 'pricequery_ro'@'localhost';
GRANT SELECT ON pricedb.products      TO 'pricequery_ro'@'localhost';
GRANT SELECT ON pricedb.price_history TO 'pricequery_ro'@'localhost';

-- Column-level grant: subscribers' email addresses are not readable at all.
GRANT SELECT (id, product_id, target_price, triggered_at) ON pricedb.price_alerts TO 'pricequery_ro'@'localhost';

-- DATABASE_URL=mysql+pymysql://pricequery_ro:<password>@localhost/pricedb
