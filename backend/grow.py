"""Grow the demo data so it looks like a real, busy Indian D2C brand. Never deletes anything.

Fills every day of the last 180 days (including today) up to its target:
~25 new customers and ~1500 orders a day (fewer on weekends, more on festivals and sales).
Days already full are skipped, so it's safe to run again: the first run fills history,
then a daily run (GitHub Actions) adds the new day and catches up on missed days.
Also: moves old pending orders to their final status, adds reviews, updates stock.

Hidden patterns (so the AI has real things to find):
- RTO (undelivered, returned) is much higher for COD, ~15% "risky" pincodes,
  new customers, Furniture, and some couriers (Shadowfax worst, Blue Dart best).
- Each product has a quality level; Webcam HD is poor and also sold at a loss.
- Some warehouses run out of stock.

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
DIGITAL = "('Software', 'Services')"

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

# First 3 digits of each city's pincodes.
PIN_PREFIX = {
    "Delhi": 110, "Lucknow": 226, "Kanpur": 208, "Varanasi": 221, "Chandigarh": 160,
    "Amritsar": 143, "Ludhiana": 141, "Jaipur": 302, "Jodhpur": 342, "Udaipur": 313,
    "Pune": 411, "Mumbai": 400, "Nagpur": 440, "Nashik": 422, "Ahmedabad": 380, "Surat": 395,
    "Vadodara": 390, "Rajkot": 360, "Chennai": 600, "Coimbatore": 641, "Madurai": 625,
    "Hyderabad": 500, "Vijayawada": 520, "Visakhapatnam": 530, "Bengaluru": 560, "Mysuru": 570,
    "Mangaluru": 575, "Kochi": 682, "Thiruvananthapuram": 695, "Kozhikode": 673, "Kolkata": 700,
    "Siliguri": 734, "Durgapur": 713, "Bhubaneswar": 751, "Cuttack": 753, "Guwahati": 781,
    "Dibrugarh": 786, "Patna": 800, "Gaya": 823, "Ranchi": 834,
}

# Final outcome of a delivered-or-not order. Placeholders are filled per query.
OUTCOME = """CASE
  WHEN {courier} IS NOT NULL AND random() < (CASE WHEN {pay} = 'COD' THEN 0.20 ELSE 0.03 END)
    * (CASE WHEN ('x' || substr(md5({pin}), 1, 2))::bit(8)::int < 40 THEN 2.2 ELSE 1 END)
    * (CASE {courier} WHEN 'Blue Dart' THEN 0.7 WHEN 'Delhivery' THEN 1.0 WHEN 'Xpressbees' THEN 1.1
                      WHEN 'Ecom Express' THEN 1.2 ELSE 1.3 END)
    * (CASE WHEN {is_new} THEN 1.3 ELSE 1 END)
    * (CASE WHEN {cat} = 'Furniture' THEN 1.2 ELSE 1 END) THEN 'rto'
  WHEN random() < 0.04 THEN 'refunded'
  ELSE 'completed' END"""

# One INSERT per day. Cheap products sell more often; pricey ones in smaller quantities.
ORDERS_SQL = f"""
WITH c AS (SELECT array_agg(id) AS ids FROM customers WHERE created_at <= %(day)s),
p AS (SELECT array_agg(id) AS ids FROM products,
        generate_series(1, CASE WHEN price_inr < 2000 THEN 6 WHEN price_inr < 20000 THEN 3 ELSE 1 END)),
o AS (
  SELECT c.ids[1 + floor(random() * cardinality(c.ids))::int] AS customer_id,
         p.ids[1 + floor(random() * cardinality(p.ids))::int] AS product_id,
         (ARRAY['Website', 'Website', 'Website', 'Amazon', 'Amazon', 'Amazon', 'Amazon',
                'Flipkart', 'Flipkart', 'Flipkart', 'Meesho'])[1 + floor(random() * 11)::int] AS channel,
         (ARRAY['Delhivery', 'Delhivery', 'Delhivery', 'Delhivery', 'Delhivery', 'Delhivery', 'Delhivery',
                'Xpressbees', 'Xpressbees', 'Xpressbees', 'Xpressbees', 'Ecom Express', 'Ecom Express',
                'Ecom Express', 'Blue Dart', 'Blue Dart', 'Blue Dart', 'Shadowfax', 'Shadowfax',
                'Shadowfax'])[1 + floor(random() * 20)::int] AS courier_pick
  FROM c, p, generate_series(1, %(n)s)
)
INSERT INTO orders (customer_id, product_id, quantity, amount_inr, channel, payment_method,
                    courier, status, order_date, delivered_date)
SELECT o.customer_id, o.product_id, q.quantity, q.quantity * pr.price_inr, o.channel, a.pay,
       a.courier, s.status, %(day)s,
       CASE WHEN s.status IN ('completed', 'refunded')
            THEN least(%(day)s + 2 + floor(random() * 5)::int, %(today)s) END
FROM o
JOIN products pr ON pr.id = o.product_id
JOIN customers cu ON cu.id = o.customer_id
CROSS JOIN LATERAL (SELECT 1 + floor(random() *
    CASE WHEN pr.price_inr > 20000 THEN 2 WHEN pr.price_inr > 2000 THEN 4 ELSE 10 END)::int AS quantity) q
CROSS JOIN LATERAL (SELECT
    CASE WHEN pr.category IN {DIGITAL} THEN 'Prepaid'
         WHEN random() < CASE o.channel WHEN 'Website' THEN 0.5 WHEN 'Amazon' THEN 0.35
                                        WHEN 'Flipkart' THEN 0.45 ELSE 0.8 END THEN 'COD'
         ELSE 'Prepaid' END AS pay,
    CASE WHEN pr.category IN {DIGITAL} THEN NULL ELSE o.courier_pick END AS courier) a
CROSS JOIN LATERAL (SELECT
    CASE WHEN a.courier IS NOT NULL AND random() < %(pending)s THEN 'pending'
         ELSE {OUTCOME.format(courier="a.courier", pay="a.pay", pin="cu.pincode",
                              is_new="%(day)s - cu.created_at < 30", cat="pr.category")} END AS status) s
"""

# Pending orders older than a week reach their final status.
SETTLE_SQL = f"""
UPDATE orders o SET status = {OUTCOME.format(courier="o.courier", pay="o.payment_method", pin="cu.pincode",
                                             is_new="o.order_date - cu.created_at < 30", cat="pr.category")}
FROM customers cu, products pr
WHERE o.status = 'pending' AND o.order_date < %(today)s - 7
  AND cu.id = o.customer_id AND pr.id = o.product_id
"""
DELIVERED_SQL = """
UPDATE orders SET delivered_date = least(order_date + 2 + floor(random() * 5)::int, %(today)s)
WHERE delivered_date IS NULL AND status IN ('completed', 'refunded')
"""

# ~5% of delivered orders get a review a few days later. Rating follows product quality.
REVIEWS_SQL = """
INSERT INTO reviews (order_id, product_id, rating, comment, created_at)
SELECT o.id, o.product_id, r.rating,
       (CASE r.rating
          WHEN 5 THEN ARRAY['Excellent quality, worth every rupee', 'Fast delivery, very happy',
                            'Bahut badiya product, highly recommended!', 'Value for money, will buy again']
          WHEN 4 THEN ARRAY['Good product, delivery took a bit long', 'Accha hai, price thoda zyada hai',
                            'Works well, box was slightly damaged', 'Nice quality, satisfied']
          WHEN 3 THEN ARRAY['Average, expected better quality', 'Theek thaak hai',
                            'Okay product but late delivery', 'Packaging was poor, product is fine']
          WHEN 2 THEN ARRAY['Quality not as shown in pictures', 'Stopped working after a week',
                            'Delivery bahut late thi', 'Not worth the price']
          ELSE ARRAY['Very bad, want a refund', 'Received a damaged product',
                     'Bekaar quality, paisa waste', 'Wrong item delivered, support not responding']
        END)[1 + floor(random() * 4)::int],
       least(o.delivered_date + 1 + floor(random() * 5)::int, %(today)s)
FROM orders o
JOIN products pr ON pr.id = o.product_id
CROSS JOIN LATERAL (SELECT greatest(1, least(5, round(
    CASE WHEN pr.name = 'Webcam HD' THEN 2.6
         ELSE 3.3 + 1.3 * ('x' || substr(md5(pr.name), 1, 2))::bit(8)::int / 255.0 END
    - CASE WHEN o.status = 'refunded' THEN 1.5 ELSE 0 END
    + (random() + random() + random() - 1.5) * 1.6)))::int AS rating) r
WHERE o.status IN ('completed', 'refunded') AND o.delivered_date IS NOT NULL
  AND o.order_date <= %(today)s - 8
  AND o.order_date > (SELECT coalesce(max(o2.order_date), '1900-01-01')
                      FROM reviews rv JOIN orders o2 ON o2.id = rv.order_id)
  AND random() < 0.05
"""

# Units sold per product + region: on one day, and the 30-day daily average.
SALES_BY_REGION = """
SELECT o.product_id, cu.region,
       coalesce(sum(o.quantity) FILTER (WHERE o.order_date = %(day)s), 0) AS sold,
       sum(o.quantity) / 30.0 AS avg30
FROM orders o JOIN customers cu ON cu.id = o.customer_id
WHERE o.order_date > %(day)s - 30 AND o.order_date <= %(day)s
GROUP BY 1, 2
"""
INVENTORY_INIT_SQL = f"""
INSERT INTO inventory (product_id, region, on_hand, reorder_point, lead_time_days, updated_at)
SELECT s.product_id, s.region,
       CASE WHEN random() < 0.08 THEN 0 ELSE round(s.avg30 * (3 + random() * 40)) END,
       greatest(1, round(s.avg30 * 7)), 3 + floor(random() * 8)::int, %(day)s
FROM ({SALES_BY_REGION}) s JOIN products pr ON pr.id = s.product_id
WHERE pr.category NOT IN {DIGITAL}
"""
# A day passes: sold units leave; low stock gets restocked ~30%% of days (suppliers are slow).
INVENTORY_DAY_SQL = f"""
UPDATE inventory i SET
  on_hand = greatest(0, i.on_hand - s.sold) + CASE
    WHEN greatest(0, i.on_hand - s.sold) < i.reorder_point AND random() < 0.3
    THEN greatest(1, round(s.avg30 * 30))::int ELSE 0 END,
  updated_at = %(day)s
FROM ({SALES_BY_REGION}) s
WHERE s.product_id = i.product_id AND s.region = i.region AND i.updated_at < %(day)s
"""


def order_target(day: date, festivals: list[tuple]) -> int:
    """Deterministic per day, so a re-run sees the same target and adds nothing."""
    boost = max([float(b) for start, end, b in festivals if start <= day <= end], default=1.0)
    trend = 1 + 0.0005 * (day - date(2026, 1, 1)).days  # slow business growth
    return round(ORDERS_PER_DAY * WEEKDAY[day.weekday()] * boost * trend)


def pending_share(age_days: int) -> float:
    """Recent orders are still pending; older ones got delivered (or not)."""
    return 0.6 if age_days <= 1 else 0.3 if age_days <= 3 else 0.1 if age_days <= 7 else 0.0


def pincode(city: str) -> str:
    return f"{PIN_PREFIX[city]}{random.randint(1, 40):03d}"  # ~40 pincodes per city


def phone() -> str:
    return f"+91 {random.choice('6789')}{random.randint(0, 10**9 - 1):09d}"


def new_customers(n: int, day: date) -> list[tuple]:
    rows = []
    for _ in range(n):
        region = random.choice(["North", "South", "East", "West"])  # even split, whatever the group count
        cities, firsts, lasts = random.choice([g for g in GROUPS if any(r == region for _, r in g[0])])
        city = random.choice([c for c, r in cities if r == region])
        name = f"{random.choice(firsts.split())} {random.choice(lasts.split())}"
        email = f"{name.lower().replace(' ', '.')}.{random.randint(100, 99999)}@example.in"
        rows.append((name, email, phone(), city, region, pincode(city), day))
    return rows


def main() -> None:
    # .strip(): a secret pasted into GitHub often ends in a hidden newline
    with psycopg.connect(os.environ["DATABASE_URL"].strip()) as conn:
        today = conn.execute("SELECT current_date").fetchone()[0]  # the DB's "today"
        start = today - timedelta(days=DAYS - 1)
        festivals = conn.execute("SELECT start_date, end_date, boost FROM festivals").fetchall()
        # Founding customers (seed.sql) get a phone + pincode.
        missing = conn.execute("SELECT id, city FROM customers WHERE pincode IS NULL").fetchall()
        conn.cursor().executemany("UPDATE customers SET phone = %s, pincode = %s WHERE id = %s",
                                  [(phone(), pincode(city), cid) for cid, city in missing])
        # Indexes keep the agent's queries fast on big tables.
        for sql in ("CREATE INDEX IF NOT EXISTS orders_order_date ON orders (order_date)",
                    "CREATE INDEX IF NOT EXISTS orders_customer_id ON orders (customer_id)",
                    "CREATE INDEX IF NOT EXISTS orders_product_id ON orders (product_id)",
                    "CREATE INDEX IF NOT EXISTS customers_region ON customers (region)",
                    "CREATE INDEX IF NOT EXISTS reviews_order_id ON reviews (order_id)"):
            conn.execute(sql)
        have_orders = dict(conn.execute(
            "SELECT order_date, count(*) FROM orders WHERE order_date >= %s GROUP BY 1", (start,)))
        have_customers = dict(conn.execute(
            "SELECT created_at, count(*) FROM customers WHERE created_at >= %s GROUP BY 1", (start,)))
        conn.commit()

        added_c = added_o = 0
        for i in range(DAYS):
            day = start + timedelta(days=i)
            need_c = CUSTOMERS_PER_DAY - have_customers.get(day, 0)
            need_o = order_target(day, festivals) - have_orders.get(day, 0)
            if need_c > 0:
                conn.cursor().executemany(
                    "INSERT INTO customers (name, email, phone, city, region, pincode, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                    new_customers(need_c, day))
                added_c += need_c
            if need_o > 0:
                conn.execute(ORDERS_SQL, {"day": day, "today": today, "n": need_o,
                                          "pending": pending_share((today - day).days)})
                added_o += need_o
            conn.commit()  # per day, so an interrupted run keeps its progress
            if need_o > 0 and i % 30 == 0:
                print(f"  ...{day}")

        conn.execute(SETTLE_SQL, {"today": today})
        conn.execute(DELIVERED_SQL, {"today": today})
        added_r = conn.execute(REVIEWS_SQL, {"today": today}).rowcount

        last = conn.execute("SELECT max(updated_at) FROM inventory").fetchone()[0]
        if last is None:
            conn.execute(INVENTORY_INIT_SQL, {"day": today})
        else:
            for n in range(1, (today - last).days + 1):
                conn.execute(INVENTORY_DAY_SQL, {"day": last + timedelta(days=n)})
        conn.commit()

        conn.execute("ANALYZE")
        total_c, total_o, out = conn.execute(
            "SELECT (SELECT count(*) FROM customers), (SELECT count(*) FROM orders), "
            "(SELECT count(*) FROM inventory WHERE on_hand = 0)").fetchone()
    print(f"Added {added_c} customers, {added_o} orders, {added_r} reviews. "
          f"Now {total_c} customers, {total_o} orders; {out} warehouse items out of stock.")


if __name__ == "__main__":
    main()
