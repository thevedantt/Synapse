"""Postgres MCP server: read-only SQL access to the sales database.

Every query runs logged in as the asking user's restricted DB role (see security.sql),
so the database itself decides which rows/columns they can see. The user comes from
MCP request metadata set by the API, never from the LLM.
"""

import json
import os

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from psycopg.rows import dict_row

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
AGENT_DB_PASSWORD = os.environ["AGENT_DB_PASSWORD"]
MAX_ROWS = 200
REGIONS = {"North", "South", "East", "West"}

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


def query(ctx: Context, sql: str, params: tuple = ()) -> str:
    # ponytail: new connection per call, add psycopg_pool if latency matters
    with psycopg.connect(
        DATABASE_URL, user=db_role(ctx), password=AGENT_DB_PASSWORD,
        row_factory=dict_row, connect_timeout=10,
    ) as conn:
        conn.read_only = True  # extra layer: DB rejects writes in this transaction
        conn.execute("SET LOCAL statement_timeout = '10s'")
        cur = conn.execute(sql, params)
        rows = cur.fetchmany(MAX_ROWS + 1)
    result = {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}
    return json.dumps(result, default=str)  # default=str handles Decimal/date


@mcp.tool()
def list_tables(ctx: Context) -> str:
    """List the tables you have access to."""
    return query(
        ctx,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name",
    )


@mcp.tool()
def describe_table(table: str, ctx: Context) -> str:
    """Show the columns (that you have access to) of a table. Call before writing SQL."""
    return query(
        ctx,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )


@mcp.tool()
def run_sql(sql: str, ctx: Context) -> str:
    """Run a read-only SQL query (SELECT only). Returns up to 200 rows as JSON.
    Prefer aggregating in SQL (SUM, GROUP BY) over fetching raw rows.
    Results only include data the current user is allowed to see."""
    try:
        return query(ctx, sql)
    except psycopg.Error as e:  # includes permission errors, connection errors, timeouts
        # Return the error to the LLM so it can fix its SQL and retry.
        return json.dumps({"error": str(e).strip()})


if __name__ == "__main__":
    mcp.run()
