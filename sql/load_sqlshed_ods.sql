-- Adapted from SQL Shed CC0 official schema; executed inside one transaction.
CREATE SCHEMA ods;
SET LOCAL search_path TO ods;
-- Ecommerce Orders
--
-- Customers, products, orders, line items, payments and returns for an online garden retailer.
--
-- Run this from the directory holding the data files:
--   duckdb -c ".read ecommerce_orders.schema.sql"

CREATE TABLE categories (
  category_id INTEGER NOT NULL,
  category_name VARCHAR NOT NULL,
  parent_category_id INTEGER,
  PRIMARY KEY (category_id)
  -- parent_category_id references categories.category_id (self-referencing,
  -- not declared, because DuckDB's COPY cannot satisfy it on load)
);

CREATE TABLE customers (
  customer_id INTEGER NOT NULL,
  first_name VARCHAR NOT NULL,
  last_name VARCHAR NOT NULL,
  email VARCHAR NOT NULL,
  city VARCHAR NOT NULL,
  country VARCHAR NOT NULL,
  signup_date DATE NOT NULL,
  loyalty_tier VARCHAR,
  marketing_opt_in BOOLEAN NOT NULL,
  PRIMARY KEY (customer_id)
);

CREATE TABLE products (
  product_id INTEGER NOT NULL,
  sku VARCHAR NOT NULL,
  product_name VARCHAR NOT NULL,
  category_id INTEGER NOT NULL,
  unit_price NUMERIC(12,2) NOT NULL,
  unit_cost NUMERIC(12,2) NOT NULL,
  discontinued_at DATE,
  PRIMARY KEY (product_id),
  FOREIGN KEY (category_id) REFERENCES categories (category_id)
);

CREATE TABLE orders (
  order_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL,
  ordered_at TIMESTAMP NOT NULL,
  shipped_at TIMESTAMP,
  status VARCHAR NOT NULL,
  channel VARCHAR NOT NULL,
  shipping_country VARCHAR NOT NULL,
  coupon_code VARCHAR,
  PRIMARY KEY (order_id),
  FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
);

CREATE TABLE order_items (
  order_item_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  unit_price NUMERIC(12,2) NOT NULL,
  discount_amount NUMERIC(12,2) NOT NULL,
  PRIMARY KEY (order_item_id),
  FOREIGN KEY (order_id) REFERENCES orders (order_id),
  FOREIGN KEY (product_id) REFERENCES products (product_id)
);

CREATE TABLE payments (
  payment_id INTEGER NOT NULL,
  order_id INTEGER NOT NULL,
  method VARCHAR NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  paid_at TIMESTAMP NOT NULL,
  status VARCHAR NOT NULL,
  failure_reason VARCHAR,
  PRIMARY KEY (payment_id),
  FOREIGN KEY (order_id) REFERENCES orders (order_id)
);

CREATE TABLE returns (
  return_id INTEGER NOT NULL,
  order_item_id INTEGER NOT NULL,
  returned_at TIMESTAMP NOT NULL,
  reason VARCHAR NOT NULL,
  refund_amount NUMERIC(12,2) NOT NULL,
  restocked BOOLEAN NOT NULL,
  PRIMARY KEY (return_id),
  FOREIGN KEY (order_item_id) REFERENCES order_items (order_item_id)
);
