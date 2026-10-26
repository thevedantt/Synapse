"""Postgres MCP server: read-only SQL access to the sales database."""

import json
import os

import psycopg
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from psycopg.rows import dict_row

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
MAX_ROWS = 200

mcp = MCPServer("postgres", instructions="Read-only access to the company sales database.")


def query(sql: str, params: tuple = ()) -> str:
    # ponytail: new connection per call, add psycopg_pool if latency matters
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10) as conn:
        conn.read_only = True  # DB itself rejects writes, whatever SQL the LLM sends
        conn.execute("SET LOCAL statement_timeout = '10s'")
        cur = conn.execute(sql, params)
        rows = cur.fetchmany(MAX_ROWS + 1)
    result = {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}
    return json.dumps(result, default=str)  # default=str handles Decimal/date


@mcp.tool()
def list_tables() -> str:
    """List all tables in the database."""
    return query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' ORDER BY table_name"
    )


@mcp.tool()
def describe_table(table: str) -> str:
    """Show columns and types of a table. Call before writing SQL against it."""
    return query(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )


@mcp.tool()
def run_sql(sql: str) -> str:
    """Run a read-only SQL query (SELECT only). Returns up to 200 rows as JSON.
    Prefer aggregating in SQL (SUM, GROUP BY) over fetching raw rows."""
    try:
        return query(sql)
    except psycopg.Error as e:  # includes connection errors/timeouts
        # Return the error to the LLM so it can fix its SQL and retry.
        return json.dumps({"error": str(e).strip()})


if __name__ == "__main__":
    mcp.run()
