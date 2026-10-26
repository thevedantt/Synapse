-- Fake sales data. Dates are relative to today so "last month" always has data.
DROP TABLE IF EXISTS orders, products, customers CASCADE;

CREATE TABLE customers (
  id serial PRIMARY KEY,
  name text NOT NULL,
  email text UNIQUE NOT NULL,
  region text NOT NULL,
  created_at date NOT NULL
);

CREATE TABLE products (
  id serial PRIMARY KEY,
  name text NOT NULL,
  category text NOT NULL,
  price numeric(10, 2) NOT NULL CHECK (price > 0)
);

CREATE TABLE orders (
  id serial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customers (id),
  product_id int NOT NULL REFERENCES products (id),
  quantity int NOT NULL CHECK (quantity > 0),
  amount numeric(12, 2) NOT NULL,
  status text NOT NULL CHECK (status IN ('completed', 'pending', 'refunded')),
  order_date date NOT NULL
);

SELECT setseed(0.42);

INSERT INTO products (name, category, price) VALUES
  ('Laptop Pro 14', 'Electronics', 1299.00),
  ('Wireless Mouse', 'Electronics', 29.99),
  ('4K Monitor', 'Electronics', 449.00),
  ('Office Chair', 'Furniture', 249.00),
  ('Standing Desk', 'Furniture', 599.00),
  ('Notebook Pack', 'Stationery', 12.50),
  ('Gel Pens (10)', 'Stationery', 8.99),
  ('CRM Pro License', 'Software', 99.00),
  ('Analytics Suite', 'Software', 199.00),
  ('Support Plan', 'Services', 149.00);

INSERT INTO customers (name, email, region, created_at)
SELECT
  'Customer ' || i,
  'customer' || i || '@example.com',
  (ARRAY['North', 'South', 'East', 'West'])[1 + floor(random() * 4)::int],
  current_date - floor(random() * 365)::int
FROM generate_series(1, 200) AS i;

INSERT INTO orders (customer_id, product_id, quantity, amount, status, order_date)
SELECT o.customer_id, o.product_id, o.quantity, o.quantity * p.price, o.status, o.order_date
FROM (
  SELECT
    1 + floor(random() * 200)::int AS customer_id,
    1 + floor(random() * 10)::int AS product_id,
    1 + floor(random() * 5)::int AS quantity,
    (ARRAY['completed', 'completed', 'completed', 'pending', 'refunded'])[1 + floor(random() * 5)::int] AS status,
    current_date - floor(random() * 180)::int AS order_date
  FROM generate_series(1, 3000)
) AS o
JOIN products AS p ON p.id = o.product_id;
