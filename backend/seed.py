"""Load fake sales data (seed.sql) and access rules (security.sql) into DATABASE_URL.
Wipes and recreates the sales tables. Creates AGENT_DB_PASSWORD in .env if missing."""

import os
import secrets
from pathlib import Path

import psycopg
from dotenv import load_dotenv

here = Path(__file__).parent
load_dotenv(here / ".env")

password = os.environ.get("AGENT_DB_PASSWORD")
if not password:
    # Password shared by the agent's per-role DB logins (see security.sql).
    password = secrets.token_urlsafe(24)
    with open(here / ".env", "a", encoding="utf-8") as f:
        f.write(f"\n# Password for the agent's restricted DB logins (created by seed.py)\nAGENT_DB_PASSWORD={password}\n")
    print("Added AGENT_DB_PASSWORD to .env")

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute("SELECT set_config('synapse.agent_password', %s, false)", (password,))
    for name in ("seed.sql", "security.sql"):
        conn.execute((here / name).read_text(encoding="utf-8"))
    count = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
print(f"Seeded {count} orders, with roles and row-level security.")
print("Now run grow.py to fill 180 days of big, realistic data.")
