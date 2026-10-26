"""Load seed.sql into the database at DATABASE_URL. Wipes existing sales tables."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute(Path(__file__).with_name("seed.sql").read_text())
    count = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
print(f"Seeded {count} orders.")
