"""Grow the demo data so it looks like a real, busy company. Never deletes anything.

Fills every day of the last 180 days (including today) up to its target:
~25 new customers and ~1500 orders a day (fewer on weekends, more in festive season).
Days already full are skipped, so it's safe to run again: the first run tops up history,
then a daily run (GitHub Actions) just adds the new day, and catches up on missed days.

Run: uv run python grow.py
"""

import os
import random
from datetime import date, timedelta

import psycopg
from dotenv import load_dotenv

load_dotenv()
DAYS = 180
CUSTOMERS_PER_DAY = 25
ORDERS_PER_DAY = 1500
WEEKDAY = [1.05, 1.05, 1.0, 1.0, 1.1, 0.85, 0.7]  # Mon..Sun

# (cities with region, first names, surnames): names match the region's culture.
GROUPS = [
    ([("Delhi", "North"), ("Lucknow", "North"), ("Kanpur", "North"), ("Varanasi", "North")],
     "Aarav Vivaan Aditya Ananya Diya Ishaan Kavya Rohan Shreya Nikhil Pooja Rahul Sneha Amit Ritu",
     "Sharma Verma Gupta Agarwal Srivastava Tripathi Mishra Pandey Saxena Tiwari"),
    ([("Chandigarh", "North"), ("Amritsar", "North"), ("Ludhiana", "North")],
     "Harpreet Gurpreet Simran Manpreet Jaskaran Navjot Amrit Harleen Kuldeep Baljeet Gurleen Arjun",
     "Singh Kaur Gill Sandhu Dhillon Sidhu Grewal Bajwa Bhatia Malhotra"),
    ([("Jaipur", "North"), ("Jodhpur", "North"), ("Udaipur", "North")],
     "Rajveer Devendra Priyanka Mahendra Kiran Yashpal Bhavna Vikram Lokesh Suman Hemant",
     "Rathore Shekhawat Choudhary Meena Chauhan Jain Bhati Rajpurohit"),
    ([("Lucknow", "North"), ("Mumbai", "West"), ("Hyderabad", "South"), ("Kolkata", "East")],
     "Ayesha Farhan Imran Zoya Sana Arif Nadia Faisal Rehan Mehwish Salman Huma",
     "Khan Siddiqui Ansari Qureshi Shaikh Sayyed Merchant Hussain"),
    ([("Pune", "West"), ("Mumbai", "West"), ("Nagpur", "West"), ("Nashik", "West")],
     "Omkar Sayali Aniket Tanvi Siddharth Rutuja Prathamesh Gauri Sanket Mrunal Tejas Aditi",
     "Deshpande Kulkarni Patil Joshi Gokhale Jadhav Shinde Pawar Bhosale Chavan"),
    ([("Ahmedabad", "West"), ("Surat", "West"), ("Vadodara", "West"), ("Rajkot", "West")],
     "Hetal Jignesh Krupa Bhavesh Nirali Dhruv Foram Parth Riddhi Chirag Khushbu Harsh",
     "Shah Patel Desai Mehta Parekh Trivedi Modi Thakkar Vyas"),
    ([("Chennai", "South"), ("Coimbatore", "South"), ("Madurai", "South")],
     "Karthik Lakshmi Arvind Divya Priya Senthil Meenakshi Vignesh Gayathri Surya Harini",
     "Subramanian Venkatesan Raghavan Ramasamy Krishnan Murugan Iyer Natarajan"),
    ([("Hyderabad", "South"), ("Vijayawada", "South"), ("Visakhapatnam", "South")],
     "Srinivas Sravani Venkat Lavanya Ravi Anusha Mahesh Swathi Pavan Keerthi Naveen",
     "Reddy Rao Naidu Chowdary Varma Goud Raju"),
    ([("Bengaluru", "South"), ("Mysuru", "South"), ("Mangaluru", "South")],
     "Chaitra Nikhil Pooja Darshan Sahana Prajwal Rakshitha Manjunath Shwetha Kiran",
     "Gowda Hegde Shetty Rao Kamath Bhat Pai Acharya"),
    ([("Kochi", "South"), ("Thiruvananthapuram", "South"), ("Kozhikode", "South")],
     "Anoop Aswathy Arjun Anjali Vishnu Lekshmi Rahul Gopika Sreejith Athira",
     "Menon Nair Pillai Kurup Varghese Thomas Panicker"),
    ([("Kolkata", "East"), ("Siliguri", "East"), ("Durgapur", "East")],
     "Sourav Ritika Arindam Payel Debjani Anirban Moumita Subhajit Tanushree Sayan",
     "Banerjee Chatterjee Mukherjee Ghosh Bose Das Sen Dutta Roy"),
    ([("Bhubaneswar", "East"), ("Cuttack", "East")],
     "Subhashree Debasish Bikash Smruti Sambit Lipsa Pradeep Sasmita",
     "Mohanty Patnaik Sahoo Mishra Panda Nayak Behera"),
    ([("Guwahati", "East"), ("Dibrugarh", "East")],
     "Pranjal Nilakshi Bhaskar Jonali Dhruba Rimjim Kaustav Pallavi",
     "Baruah Bora Gogoi Saikia Kalita Hazarika"),
    ([("Patna", "East"), ("Gaya", "East"), ("Ranchi", "East")],
     "Rakesh Anamika Manish Kumari Abhishek Priti Santosh Nisha Ranjeet",
     "Sinha Jha Prasad Yadav Thakur Choudhary Mahto"),
]

# Added once (if missing) on top of the 10 products in seed.sql.
MORE_PRODUCTS = [
    ("Laptop Air 13", "Electronics", 89999), ("Mechanical Keyboard", "Electronics", 5499),
    ("Noise-Cancelling Headphones", "Electronics", 24999), ("USB-C Dock", "Electronics", 8999),
    ("Webcam HD", "Electronics", 3999), ("Tablet 11", "Electronics", 45999),
    ("Ergonomic Chair Plus", "Furniture", 24999), ("Filing Cabinet", "Furniture", 11499),
    ("Meeting Table", "Furniture", 54999), ("Bookshelf", "Furniture", 7999),
    ("Printer Paper (5 reams)", "Stationery", 1499), ("Whiteboard", "Stationery", 2999),
    ("Sticky Notes Pack", "Stationery", 249), ("Desk Organiser", "Stationery", 899),
    ("HR Payroll License", "Software", 9999), ("Cloud Backup (1 yr)", "Software", 4999),
    ("Antivirus Business", "Software", 2999), ("Installation Service", "Services", 4999),
    ("Annual Maintenance", "Services", 19999), ("Staff Training Day", "Services", 29999),
]

# One INSERT per day. Cheap products sell more often; pricey ones in smaller quantities.
ORDERS_SQL = """
WITH c AS (SELECT array_agg(id) AS ids FROM customers WHERE created_at <= %(day)s),
p AS (SELECT array_agg(id) AS ids FROM products,
        generate_series(1, CASE WHEN price_inr < 2000 THEN 6 WHEN price_inr < 20000 THEN 3 ELSE 1 END)),
o AS (
  SELECT c.ids[1 + floor(random() * cardinality(c.ids))::int] AS customer_id,
         p.ids[1 + floor(random() * cardinality(p.ids))::int] AS product_id
  FROM c, p, generate_series(1, %(n)s)
)
INSERT INTO orders (customer_id, product_id, quantity, amount_inr, status, order_date)
SELECT o.customer_id, o.product_id, q.quantity, q.quantity * pr.price_inr,
       CASE WHEN random() < 0.04 THEN 'refunded'
            WHEN random() < %(pending)s THEN 'pending' ELSE 'completed' END,
       %(day)s
FROM o JOIN products pr ON pr.id = o.product_id
CROSS JOIN LATERAL (SELECT 1 + floor(random() *
    CASE WHEN pr.price_inr > 20000 THEN 2 WHEN pr.price_inr > 2000 THEN 4 ELSE 10 END)::int
    AS quantity) q
"""


def order_target(day: date) -> int:
    """Deterministic per day, so a re-run sees the same target and adds nothing."""
    festive = 1.2 if day.month in (10, 11) else 1.0  # Diwali season
    trend = 1 + 0.0005 * (day - date(2026, 1, 1)).days  # slow business growth
    return round(ORDERS_PER_DAY * WEEKDAY[day.weekday()] * festive * trend)


def pending_share(age_days: int) -> float:
    """Recent orders are still pending; older ones got completed."""
    return 0.5 if age_days <= 1 else 0.25 if age_days <= 3 else 0.1 if age_days <= 7 else 0.0


def new_customers(n: int, day: date) -> list[tuple]:
    rows = []
    for _ in range(n):
        region = random.choice(["North", "South", "East", "West"])  # even split, whatever the group count
        cities, firsts, lasts = random.choice([g for g in GROUPS if any(r == region for _, r in g[0])])
        city = random.choice([c for c, r in cities if r == region])
        name = f"{random.choice(firsts.split())} {random.choice(lasts.split())}"
        email = f"{name.lower().replace(' ', '.')}.{random.randint(100, 99999)}@example.in"
        rows.append((name, email, city, region, day))
    return rows


def get_database_url() -> str:
    return os.environ["DATABASE_URL"].strip()


def main() -> None:
    with psycopg.connect(get_database_url()) as conn:
        today = conn.execute("SELECT current_date").fetchone()[0]  # the DB's "today"
        start = today - timedelta(days=DAYS - 1)
        if conn.execute("SELECT count(*) FROM products").fetchone()[0] < 30:
            conn.cursor().executemany(
                "INSERT INTO products (name, category, price_inr) VALUES (%s, %s, %s)", MORE_PRODUCTS)
        # Indexes keep the agent's queries fast on a big table.
        conn.execute("CREATE INDEX IF NOT EXISTS orders_order_date ON orders (order_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS orders_customer_id ON orders (customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS customers_region ON customers (region)")
        have_orders = dict(conn.execute(
            "SELECT order_date, count(*) FROM orders WHERE order_date >= %s GROUP BY 1", (start,)))
        have_customers = dict(conn.execute(
            "SELECT created_at, count(*) FROM customers WHERE created_at >= %s GROUP BY 1", (start,)))
        conn.commit()

        added_c = added_o = 0
        for i in range(DAYS):
            day = start + timedelta(days=i)
            need_c = CUSTOMERS_PER_DAY - have_customers.get(day, 0)
            need_o = order_target(day) - have_orders.get(day, 0)
            if need_c > 0:
                conn.cursor().executemany(
                    "INSERT INTO customers (name, email, city, region, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                    new_customers(need_c, day))
                added_c += need_c
            if need_o > 0:
                conn.execute(ORDERS_SQL, {"day": day, "n": need_o, "pending": pending_share((today - day).days)})
                added_o += need_o
            conn.commit()  # per day, so an interrupted run keeps its progress
            if need_o > 0 and i % 30 == 0:
                print(f"  ...{day}")

        # Orders older than a week are no longer pending.
        conn.execute("UPDATE orders SET status = 'completed' "
                     "WHERE status = 'pending' AND order_date < current_date - 7")
        conn.execute("ANALYZE customers, products, orders")
        total_c, total_o = conn.execute(
            "SELECT (SELECT count(*) FROM customers), (SELECT count(*) FROM orders)").fetchone()
    print(f"Added {added_c} customers and {added_o} orders. Now {total_c} customers, {total_o} orders.")


if __name__ == "__main__":
    main()
