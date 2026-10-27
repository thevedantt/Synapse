-- Fake Indian sales data. All money in INR (₹).
-- Dates are relative to today so "last month" always has data.
DROP TABLE IF EXISTS orders, products, customers CASCADE;

CREATE TABLE customers (
  id serial PRIMARY KEY,
  name text NOT NULL,
  email text UNIQUE NOT NULL,
  city text NOT NULL,
  region text NOT NULL,
  created_at date NOT NULL
);

CREATE TABLE products (
  id serial PRIMARY KEY,
  name text NOT NULL,
  category text NOT NULL,
  price_inr numeric(12, 2) NOT NULL CHECK (price_inr > 0)
);

CREATE TABLE orders (
  id serial PRIMARY KEY,
  customer_id int NOT NULL REFERENCES customers (id),
  product_id int NOT NULL REFERENCES products (id),
  quantity int NOT NULL CHECK (quantity > 0),
  amount_inr numeric(14, 2) NOT NULL,
  status text NOT NULL CHECK (status IN ('completed', 'pending', 'refunded')),
  order_date date NOT NULL
);

SELECT setseed(0.42);

INSERT INTO products (name, category, price_inr) VALUES
  ('Laptop Pro 14', 'Electronics', 124999.00),
  ('Wireless Mouse', 'Electronics', 1299.00),
  ('4K Monitor', 'Electronics', 32999.00),
  ('Office Chair', 'Furniture', 14999.00),
  ('Standing Desk', 'Furniture', 39999.00),
  ('Notebook Pack', 'Stationery', 499.00),
  ('Gel Pens (10)', 'Stationery', 199.00),
  ('CRM Pro License', 'Software', 7999.00),
  ('Analytics Suite', 'Software', 15999.00),
  ('Support Plan', 'Services', 11999.00);

-- 50 customers, names matching their region (Hindi, Punjabi, Marathi, Gujarati,
-- Tamil, Telugu, Kannada, Malayalam, Bengali, Odia, Assamese, Bihari).
INSERT INTO customers (name, email, city, region, created_at)
SELECT name, lower(replace(name, ' ', '.')) || '@example.in', city, region,
       current_date - floor(random() * 365)::int
FROM (VALUES
  -- North
  ('Aarav Sharma', 'Delhi', 'North'),
  ('Rohit Malhotra', 'Delhi', 'North'),
  ('Simran Kaur', 'Delhi', 'North'),
  ('Neha Agarwal', 'Delhi', 'North'),
  ('Harpreet Singh Gill', 'Chandigarh', 'North'),
  ('Gurleen Kaur Sandhu', 'Chandigarh', 'North'),
  ('Manpreet Bhatia', 'Chandigarh', 'North'),
  ('Rajveer Singh Rathore', 'Jaipur', 'North'),
  ('Priyanka Choudhary', 'Jaipur', 'North'),
  ('Devendra Shekhawat', 'Jaipur', 'North'),
  ('Anjali Srivastava', 'Lucknow', 'North'),
  ('Saurabh Tripathi', 'Lucknow', 'North'),
  ('Ayesha Siddiqui', 'Lucknow', 'North'),
  -- West
  ('Omkar Deshpande', 'Pune', 'West'),
  ('Sayali Kulkarni', 'Pune', 'West'),
  ('Aniket Patil', 'Pune', 'West'),
  ('Tanvi Joshi', 'Mumbai', 'West'),
  ('Siddharth Gokhale', 'Mumbai', 'West'),
  ('Rutuja Jadhav', 'Mumbai', 'West'),
  ('Prathamesh Shinde', 'Mumbai', 'West'),
  ('Farhan Merchant', 'Mumbai', 'West'),
  ('Hetal Shah', 'Ahmedabad', 'West'),
  ('Jignesh Patel', 'Ahmedabad', 'West'),
  ('Krupa Desai', 'Ahmedabad', 'West'),
  ('Bhavesh Mehta', 'Surat', 'West'),
  ('Nirali Parekh', 'Surat', 'West'),
  -- South
  ('Karthik Subramanian', 'Chennai', 'South'),
  ('Lakshmi Venkatesan', 'Chennai', 'South'),
  ('Arvind Raghavan', 'Chennai', 'South'),
  ('Divya Ramasamy', 'Chennai', 'South'),
  ('Srinivas Reddy', 'Hyderabad', 'South'),
  ('Sravani Rao', 'Hyderabad', 'South'),
  ('Venkat Naidu', 'Hyderabad', 'South'),
  ('Mohammed Irfan Khan', 'Hyderabad', 'South'),
  ('Chaitra Gowda', 'Bengaluru', 'South'),
  ('Nikhil Hegde', 'Bengaluru', 'South'),
  ('Pooja Shetty', 'Bengaluru', 'South'),
  ('Anoop Menon', 'Kochi', 'South'),
  ('Aswathy Nair', 'Kochi', 'South'),
  -- East
  ('Sourav Banerjee', 'Kolkata', 'East'),
  ('Ritika Chatterjee', 'Kolkata', 'East'),
  ('Arindam Mukherjee', 'Kolkata', 'East'),
  ('Payel Ghosh', 'Kolkata', 'East'),
  ('Subhashree Mohanty', 'Bhubaneswar', 'East'),
  ('Debasish Patnaik', 'Bhubaneswar', 'East'),
  ('Bikash Sahoo', 'Bhubaneswar', 'East'),
  ('Pranjal Baruah', 'Guwahati', 'East'),
  ('Nilakshi Bora', 'Guwahati', 'East'),
  ('Rakesh Kumar Sinha', 'Patna', 'East'),
  ('Anamika Jha', 'Patna', 'East')
) AS c (name, city, region);

INSERT INTO orders (customer_id, product_id, quantity, amount_inr, status, order_date)
SELECT o.customer_id, o.product_id, o.quantity, o.quantity * p.price_inr, o.status, o.order_date
FROM (
  SELECT
    1 + floor(random() * 50)::int AS customer_id,
    1 + floor(random() * 10)::int AS product_id,
    1 + floor(random() * 5)::int AS quantity,
    (ARRAY['completed', 'completed', 'completed', 'pending', 'refunded'])[1 + floor(random() * 5)::int] AS status,
    current_date - floor(random() * 180)::int AS order_date
  FROM generate_series(1, 3000)
) AS o
JOIN products AS p ON p.id = o.product_id;
