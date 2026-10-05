CREATE TABLE categories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE products (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	upc VARCHAR(64) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	url VARCHAR(500) NOT NULL, 
	category_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (upc), 
	FOREIGN KEY(category_id) REFERENCES categories (id)
);

CREATE TABLE price_alerts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	product_id INTEGER NOT NULL, 
	email VARCHAR(255) NOT NULL, 
	target_price FLOAT NOT NULL, 
	triggered_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_alert_product_email_target UNIQUE (product_id, email, target_price), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);

CREATE TABLE price_history (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	product_id INTEGER NOT NULL, 
	price FLOAT NOT NULL, 
	in_stock BOOL NOT NULL, 
	scraped_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
);

CREATE INDEX ix_price_history_product_id_scraped_at ON price_history (product_id, scraped_at);
