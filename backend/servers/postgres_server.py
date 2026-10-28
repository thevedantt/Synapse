"""Postgres MCP server: read-only SQL access to the sales database.

Every query runs logged in as the asking user's restricted DB role (see security.sql),
so the database itself decides which rows/columns they can see. The user comes from
MCP request metadata set by the API, never from the LLM.

The schema (tables, columns, meanings from COMMENT ON) is also published as an MCP
resource, so the agent knows it up front instead of spending LLM calls discovering it.
"""

import json
import os
import re
from decimal import Decimal

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
AGENT_DB_PASSWORD = os.environ["AGENT_DB_PASSWORD"]
MAX_ROWS = 200
REGIONS = {"North", "South", "East", "West"}
ROLES = ["synapse_admin", "synapse_analyst"] + [f"synapse_mgr_{r.lower()}" for r in REGIONS]


def _read_only(conn: psycopg.Connection) -> None:
    conn.read_only = True  # extra layer: DB rejects writes in every transaction


# One pool per DB login: reusing open connections skips a TLS + auth handshake per query.
POOLS = {
    role: ConnectionPool(
        DATABASE_URL, min_size=1, max_size=10, configure=_read_only, open=True,
        check=ConnectionPool.check_connection,  # Neon drops idle connections: test before use
        kwargs={"user": role, "password": AGENT_DB_PASSWORD, "row_factory": dict_row, "connect_timeout": 10},
    )
    for role in ROLES
}

mcp = MCPServer("postgres", instructions="Read-only access to the company sales database.")


def db_role(ctx: Context) -> str:
    """Map the caller (from request metadata) to a DB login. Deny by default."""
    user = (ctx.request_context.meta or {}).get("synapse/user") or {}
    role, region = user.get("role"), user.get("region")
    if role in ("admin", "analyst"):
        return f"synapse_{role}"
    if role == "manager" and region in REGIONS:
        return f"synapse_mgr_{region.lower()}"
    raise PermissionError("No valid user context: access denied.")


def query(role: str, sql: str, params: tuple | None = None) -> list[dict]:
    # params=None: no placeholder parsing, so LLM SQL like LIKE '%x%' works as written.
    for attempt in range(2):
        with POOLS[role].connection(timeout=15) as conn:
            try:
                conn.execute("SET LOCAL statement_timeout = '10s'")
                return conn.execute(sql, params).fetchmany(MAX_ROWS + 1)
            except psycopg.OperationalError:
                # Neon closes idle connections; retry once on a fresh one (not on slow queries).
                if attempt or not conn.broken:
                    raise


MONEY = re.compile(r"amount|revenue|inr|sales|value|price|cost|margin|profit|gmv|aov|spend", re.I)


def inr(x: Decimal | int | float) -> str:
    """₹ in Indian style: ₹1,24,999 / ₹12.50 lakh / ₹3.21 crore. LLMs get this conversion wrong."""
    x = float(x)
    if abs(x) >= 1e7:
        return f"₹{x / 1e7:,.2f} crore"
    if abs(x) >= 1e5:
        return f"₹{x / 1e5:.2f} lakh"
    whole = f"{abs(round(x)):d}"
    head, tail = whole[:-3], whole[-3:]
    head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
    return f"{'-' if x < 0 else ''}₹{head + ',' if head else ''}{tail}"


def as_json(rows: list[dict]) -> str:
    # Money columns also get a ready-to-copy Indian-format string (e.g. revenue_fmt).
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) and MONEY.search(k):
                r[f"{k}_fmt"] = inr(v)
    result = {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}
    return json.dumps(result, default=str, ensure_ascii=False)  # default=str handles Decimal/date


@mcp.resource("postgres://schema", name="schema", mime_type="text/plain",
              description="Tables, columns and what they mean")
def schema() -> str:
    rows = query("synapse_admin", """
        SELECT c.table_name, c.column_name, c.data_type,
               obj_description(format('public.%I', c.table_name)::regclass) AS table_note,
               col_description(format('public.%I', c.table_name)::regclass, c.ordinal_position) AS note
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.table_name IN (SELECT table_name FROM information_schema.tables
                               WHERE table_schema = 'public' AND table_type = 'BASE TABLE')
        ORDER BY c.table_name, c.ordinal_position""")
    lines, table = [], None
    for r in rows:
        if r["table_name"] != table:
            table = r["table_name"]
            lines.append(f"\nTABLE {table}" + (f" -- {r['table_note']}" if r["table_note"] else ""))
        lines.append(f"  {r['column_name']} {r['data_type']}" + (f" -- {r['note']}" if r["note"] else ""))
    return "Database schema (PostgreSQL):" + "\n".join(lines)


@mcp.tool()
def describe_table(table: str, ctx: Context) -> str:
    """Show the columns (that you have access to) of a table. Only needed if unsure:
    the schema is already in your instructions."""
    return as_json(query(db_role(ctx),
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,)))


@mcp.tool()
def run_sql(sql: str, ctx: Context) -> str:
    """Run a read-only SQL query (SELECT only). Returns up to 200 rows as JSON.
    Prefer aggregating in SQL (SUM, GROUP BY) over fetching raw rows.
    Results only include data the current user is allowed to see."""
    try:
        return as_json(query(db_role(ctx), sql))
    except psycopg.Error as e:  # includes permission errors, connection errors, timeouts
        # Return the error to the LLM so it can fix its SQL and retry.
        return json.dumps({"error": str(e).strip()})


if __name__ == "__main__":
    mcp.run()
